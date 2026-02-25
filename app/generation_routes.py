"""
generation_routes.py — APIRouter con endpoints de generación/curación bajo /v1/datasets/.
"""

import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.trefa_assets import (
    ESCENARIOS,
    ESCENARIOS_TC,
    SYSTEM_PROMPT_TC,
    get_assets_summary,
)

router = APIRouter(prefix="/v1/datasets", tags=["generation"])


# ── Request models ──────────────────────────────────────

class TemplateGenRequest(BaseModel):
    output_filename: str = Field(default="", description="Nombre archivo salida (auto-generado si vacío)")
    count: Optional[int] = Field(default=None, description="Cantidad total (None = default ~160)")


class SyntheticGenRequest(BaseModel):
    output_filename: str = Field(default="")
    count: int = Field(default=100, ge=10, le=1000)
    model: str = Field(default="claude-haiku-4-5-20251001")
    scenario_filter: Optional[list[str]] = None
    pause_seconds: float = Field(default=0.3, ge=0.1, le=5.0)


class CurateRequest(BaseModel):
    synthetic_path: str
    reference_path: str
    output_train_filename: str = Field(default="")
    output_eval_filename: str = Field(default="")
    threshold: int = Field(default=6, ge=1, le=10)
    batch_size: int = Field(default=5, ge=1, le=20)
    eval_split: float = Field(default=0.1, ge=0.01, le=0.5)
    only_evaluate: bool = False


class PipelineRequest(BaseModel):
    stages: str = Field(default="todas", pattern="^(1|2|3|todas)$")
    gold_files: Optional[list[str]] = None
    count_stage1: int = Field(default=40, ge=5, le=200)
    count_stage2: int = Field(default=400, ge=10, le=2000)
    threshold: float = Field(default=7.0, ge=1.0, le=10.0)
    pause_seconds: float = Field(default=2.0, ge=0.5, le=10.0)


class GoldAmplifierRequest(BaseModel):
    gold_file: str = Field(default="", description="Ruta al archivo gold JSONL (auto-detect si vacío)")
    output_filename: str = Field(default="", description="Nombre archivo salida")
    count: int = Field(default=400, ge=10, le=2000)
    pause_seconds: float = Field(default=1.5, ge=0.5, le=10.0)


class MergeEvaluateRequest(BaseModel):
    threshold_sonnet: float = Field(default=7.5, ge=1.0, le=10.0, description="Umbral Gemini 3 Flash (evaluación)")
    threshold_gemini: float = Field(default=8.0, ge=1.0, le=10.0, description="Umbral Gemini 2.5 Flash (verificación)")
    eval_split: float = Field(default=0.10, ge=0.01, le=0.5)
    exclude_patterns: Optional[list[str]] = None
    only_tc: bool = False


class TrainingDatasetRequest(BaseModel):
    output_filename: str = Field(default="", description="Nombre archivo salida (auto-generado si vacio)")
    seed: int = Field(default=42, description="Semilla para reproducibilidad")


class AnalyzeToolsRequest(BaseModel):
    filepaths: list[str]


class UpdatePromptRequest(BaseModel):
    target_files: list[str]
    new_system_prompt: str


# ── Helper: resolver API key ──────────────────────────

def _get_api_key(request: Request, header_name: str, env_name: str) -> str:
    key = request.headers.get(header_name)
    if key:
        return key
    key = getattr(request.app.state, "settings", None)
    if key:
        settings = request.app.state.settings
        attr = env_name.lower().replace("trefa_", "")
        val = getattr(settings, attr, None)
        if val:
            return val
    key = os.environ.get(env_name)
    if key:
        return key
    raise HTTPException(
        status_code=400,
        detail=f"API key requerida. Envía header '{header_name}' o configura env var '{env_name}'."
    )


def _get_output_dir(request: Request) -> str:
    settings = getattr(request.app.state, "settings", None)
    if settings and hasattr(settings, "generation_output_dir"):
        return settings.generation_output_dir
    return os.environ.get(
        "TREFA_GENERATION_OUTPUT_DIR",
        "/Users/marianomorales/Downloads/fine-tuning/Tool Calling/generated"
    )


def _get_job_manager(request: Request):
    jm = getattr(request.app.state, "job_manager", None)
    if not jm:
        raise HTTPException(status_code=503, detail="JobManager no inicializado")
    return jm


# ── Endpoints info (sin LLM) ──────────────────────────

@router.get("/generate/scenarios")
async def list_scenarios():
    """Lista escenarios TC y no-TC disponibles."""
    tc = {k: {"descripcion": v["descripcion"], "tools_esperadas": v["tools_esperadas"], "contextos": len(v["contextos"])} for k, v in ESCENARIOS_TC.items()}
    no_tc = {k: {"descripcion": v["descripcion"], "contextos": len(v["contextos"])} for k, v in ESCENARIOS.items()}
    return {"tool_calling": tc, "sin_tool_calling": no_tc, "total_tc": len(tc), "total_no_tc": len(no_tc)}


@router.get("/generate/assets")
async def assets_summary():
    """Resumen de assets centralizados."""
    return get_assets_summary()


@router.get("/generate/current-prompt")
async def current_prompt():
    """System prompt actual."""
    return {"system_prompt_tc": SYSTEM_PROMPT_TC, "length": len(SYSTEM_PROMPT_TC)}


# ── Endpoints generación (con jobs) ───────────────────

@router.post("/generate/template")
async def generate_template(body: TemplateGenRequest, request: Request):
    """Lanzar generación por plantillas (sin LLM)."""
    from app.generators import generate_template_dataset

    jm = _get_job_manager(request)
    output_dir = _get_output_dir(request)
    os.makedirs(output_dir, exist_ok=True)

    filename = body.output_filename or f"plantillas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    output_path = os.path.join(output_dir, filename)

    job_id = jm.create_job("template_gen", {"output": filename, "count": body.count})
    jm.start_job(job_id, lambda: generate_template_dataset(jm, job_id, output_path, body.count))

    return {"job_id": job_id, "output_path": output_path}


@router.post("/generate/synthetic")
async def generate_synthetic(body: SyntheticGenRequest, request: Request):
    """Lanzar generación sintética con Claude."""
    from app.generators import generate_synthetic_dataset

    jm = _get_job_manager(request)
    api_key = _get_api_key(request, "X-Anthropic-Key", "TREFA_ANTHROPIC_API_KEY")
    output_dir = _get_output_dir(request)
    os.makedirs(output_dir, exist_ok=True)

    filename = body.output_filename or f"sintetico_{body.count}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    output_path = os.path.join(output_dir, filename)

    job_id = jm.create_job("synthetic_gen", {"output": filename, "count": body.count, "model": body.model})
    jm.start_job(job_id, lambda: generate_synthetic_dataset(
        jm, job_id, output_path, api_key,
        count=body.count, model=body.model,
        scenario_filter=body.scenario_filter, pause_seconds=body.pause_seconds
    ))

    return {"job_id": job_id, "output_path": output_path}


@router.post("/generate/curate")
async def curate(body: CurateRequest, request: Request):
    """Lanzar curación de calidad con Gemini."""
    from app.generators import curate_dataset

    jm = _get_job_manager(request)
    api_key = _get_api_key(request, "X-Gemini-Key", "TREFA_GEMINI_API_KEY")
    output_dir = _get_output_dir(request)
    os.makedirs(output_dir, exist_ok=True)

    train_fn = body.output_train_filename or f"train_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    eval_fn = body.output_eval_filename or f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    train_path = os.path.join(output_dir, train_fn)
    eval_path = os.path.join(output_dir, eval_fn)

    job_id = jm.create_job("curate", {"synthetic": body.synthetic_path, "reference": body.reference_path, "threshold": body.threshold})
    jm.start_job(job_id, lambda: curate_dataset(
        jm, job_id, body.synthetic_path, body.reference_path,
        train_path, eval_path, api_key,
        threshold=body.threshold, batch_size=body.batch_size,
        eval_split=body.eval_split, only_evaluate=body.only_evaluate
    ))

    return {"job_id": job_id, "train_path": train_path, "eval_path": eval_path}


@router.post("/generate/pipeline")
async def pipeline(body: PipelineRequest, request: Request):
    """Lanzar pipeline 3 etapas TC con Gemini."""
    from app.generators import run_toolcalling_pipeline

    jm = _get_job_manager(request)
    api_key = _get_api_key(request, "X-Gemini-Key", "TREFA_GEMINI_API_KEY")
    output_dir = _get_output_dir(request)
    working_dir = os.path.join(output_dir, f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

    job_id = jm.create_job("pipeline", {"stages": body.stages, "e1": body.count_stage1, "e2": body.count_stage2})
    jm.start_job(job_id, lambda: run_toolcalling_pipeline(
        jm, job_id, api_key, working_dir,
        stages=body.stages, gold_files=body.gold_files,
        count_stage1=body.count_stage1, count_stage2=body.count_stage2,
        threshold=body.threshold, pause_seconds=body.pause_seconds
    ))

    return {"job_id": job_id, "working_dir": working_dir}


@router.post("/generate/gold-amplifier")
async def gold_amplifier(body: GoldAmplifierRequest, request: Request):
    """Generación masiva de conversaciones TC de alta calidad con Gold Amplifier."""
    from app.generators import generate_gold_amplified

    jm = _get_job_manager(request)
    api_key = _get_api_key(request, "X-Gemini-Key", "TREFA_GEMINI_API_KEY")
    output_dir = _get_output_dir(request)
    os.makedirs(output_dir, exist_ok=True)

    # Auto-detect gold file
    gold_file = body.gold_file
    if not gold_file:
        datasets_dir = os.path.join(os.path.dirname(output_dir), "datasets")
        candidate = os.path.join(datasets_dir, "golden_qwen_mariana.jsonl")
        if os.path.exists(candidate):
            gold_file = candidate
        else:
            raise HTTPException(400, "No se encontró archivo gold. Especifica gold_file.")

    filename = body.output_filename or f"gold_amplified_{body.count}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    output_path = os.path.join(output_dir, filename)
    datasets_dir = os.path.join(os.path.dirname(output_dir), "datasets")

    job_id = jm.create_job("gold_amplifier", {"gold": os.path.basename(gold_file), "count": body.count})
    jm.start_job(job_id, lambda: generate_gold_amplified(
        jm, job_id, api_key, output_path, gold_file,
        count=body.count, pause_seconds=body.pause_seconds,
        datasets_dir=datasets_dir
    ))

    return {"job_id": job_id, "output_path": output_path}


@router.post("/generate/merge-evaluate")
async def merge_evaluate(body: MergeEvaluateRequest, request: Request):
    """Merge & Evaluate: fusiona todos los datasets, evalúa con Gemini 3 Flash + Gemini 2.5 Flash."""
    from app.generators import merge_and_evaluate

    jm = _get_job_manager(request)
    gemini_key = _get_api_key(request, "X-Gemini-Key", "TREFA_GEMINI_API_KEY")
    output_dir = _get_output_dir(request)
    os.makedirs(output_dir, exist_ok=True)

    # Resolver dataset_dirs: output_dir + datasets hermano
    dataset_dirs = [output_dir]
    datasets_sibling = os.path.join(os.path.dirname(output_dir), "datasets")
    if os.path.isdir(datasets_sibling):
        dataset_dirs.append(datasets_sibling)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    train_path = os.path.join(output_dir, f"train_{ts}.jsonl")
    eval_path = os.path.join(output_dir, f"eval_{ts}.jsonl")

    job_id = jm.create_job("merge_evaluate", {
        "threshold_eval": body.threshold_sonnet,
        "threshold_verify": body.threshold_gemini,
        "eval_split": body.eval_split,
        "only_tc": body.only_tc,
        "dataset_dirs": [os.path.basename(d) for d in dataset_dirs],
    })
    jm.start_job(job_id, lambda: merge_and_evaluate(
        jm, job_id,
        gemini_api_key=gemini_key,
        output_train_path=train_path,
        output_eval_path=eval_path,
        dataset_dirs=dataset_dirs,
        threshold_eval=body.threshold_sonnet,
        threshold_verify=body.threshold_gemini,
        eval_split=body.eval_split,
        exclude_patterns=body.exclude_patterns,
        only_tc=body.only_tc,
    ))

    return {"job_id": job_id, "train_path": train_path, "eval_path": eval_path}


# ── Endpoints training ─────────────────────────────────

@router.post("/generate/training-dataset")
async def generate_training_dataset(body: TrainingDatasetRequest, request: Request):
    """Genera dataset de entrenamiento con conversaciones plantilla (25 escenarios, sin LLM)."""
    from training.generar_conversaciones import (
        gen_saludo_simple, gen_busqueda_exitosa, gen_sin_inventario,
        gen_financiamiento, gen_info_financiamiento, gen_garantia,
        gen_ubicaciones, gen_intercambio, gen_estadisticas, gen_comparacion,
        gen_cotizacion_email, gen_fuera_de_tema, gen_eres_bot,
        gen_no_negociar, gen_no_consejo_legal, gen_cliente_frustrado,
        gen_documentos, gen_devoluciones, gen_proceso_compra,
        gen_cita_prueba, gen_flujo_completo, gen_flujo_con_email,
        gen_ambiguo, gen_detalle_vehiculo, gen_ortografia_real,
    )
    import random as _random
    import json as _json

    jm = _get_job_manager(request)
    output_dir = _get_output_dir(request)
    os.makedirs(output_dir, exist_ok=True)

    filename = body.output_filename or f"training_dataset_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    output_path = os.path.join(output_dir, filename)

    async def _run():
        _random.seed(body.seed)
        generators = [
            gen_saludo_simple, gen_busqueda_exitosa, gen_sin_inventario,
            gen_financiamiento, gen_info_financiamiento, gen_garantia,
            gen_ubicaciones, gen_intercambio, gen_estadisticas, gen_comparacion,
            gen_cotizacion_email, gen_fuera_de_tema, gen_eres_bot,
            gen_no_negociar, gen_no_consejo_legal, gen_cliente_frustrado,
            gen_documentos, gen_devoluciones, gen_proceso_compra,
            gen_cita_prueba, gen_flujo_completo, gen_flujo_con_email,
            gen_ambiguo, gen_detalle_vehiculo, gen_ortografia_real,
        ]
        all_convos = []
        for i, gen_func in enumerate(generators):
            jm.update_progress(job_id, int((i / len(generators)) * 90), f"Generando: {gen_func.__name__}")
            convos = gen_func()
            all_convos.extend(convos)

        _random.shuffle(all_convos)

        with open(output_path, "w", encoding="utf-8") as f:
            for c in all_convos:
                f.write(_json.dumps(c, ensure_ascii=False) + "\n")

        jm.update_progress(job_id, 100, f"Completado: {len(all_convos)} conversaciones")
        return {"total": len(all_convos), "output": output_path}

    job_id = jm.create_job("training_dataset", {"output": filename, "seed": body.seed})
    jm.start_job(job_id, _run)

    return {"job_id": job_id, "output_path": output_path}


@router.get("/training/system-prompt")
async def get_training_system_prompt():
    """Obtiene el system prompt optimizado para fine-tuning."""
    prompt_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "training", "system_prompt_finetuning.txt")
    if not os.path.exists(prompt_path):
        raise HTTPException(404, "system_prompt_finetuning.txt no encontrado")
    with open(prompt_path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"system_prompt": content, "length": len(content), "path": prompt_path}


@router.get("/training/script")
async def get_training_script():
    """Obtiene el script de fine-tuning para vast.ai."""
    script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "training", "train_qwen3_vast.py")
    if not os.path.exists(script_path):
        raise HTTPException(404, "train_qwen3_vast.py no encontrado")
    with open(script_path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"script": content, "path": script_path, "lines": content.count("\n") + 1}


# ── Endpoints análisis ────────────────────────────────

@router.post("/analyze/tools")
async def analyze_tools(body: AnalyzeToolsRequest, request: Request):
    """Analizar tool patterns en archivos JSONL."""
    from app.analyzers import analyze_tool_patterns_batch

    jm = _get_job_manager(request)
    for fp in body.filepaths:
        if not os.path.exists(fp):
            raise HTTPException(status_code=404, detail=f"Archivo no encontrado: {fp}")

    job_id = jm.create_job("analyze", {"files": [os.path.basename(f) for f in body.filepaths]})
    jm.start_job(job_id, lambda: analyze_tool_patterns_batch(jm, job_id, body.filepaths))

    return {"job_id": job_id}


@router.post("/analyze/update-prompt")
async def update_prompt(body: UpdatePromptRequest, request: Request):
    """Actualizar system prompt en batch."""
    from app.analyzers import update_system_prompt_batch

    jm = _get_job_manager(request)
    for fp in body.target_files:
        if not os.path.exists(fp):
            raise HTTPException(status_code=404, detail=f"Archivo no encontrado: {fp}")

    job_id = jm.create_job("update_prompt", {"files": len(body.target_files)})
    jm.start_job(job_id, lambda: update_system_prompt_batch(jm, job_id, body.target_files, body.new_system_prompt))

    return {"job_id": job_id}


# ── Endpoints jobs ────────────────────────────────────

@router.get("/jobs")
async def list_jobs(request: Request, type: Optional[str] = None, limit: int = 20):
    """Listar jobs recientes."""
    jm = _get_job_manager(request)
    jobs = jm.list_jobs(job_type=type, limit=limit)
    return {"jobs": [j.model_dump() for j in jobs], "count": len(jobs)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    """Estado detallado de un job."""
    jm = _get_job_manager(request)
    job = jm.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job.model_dump()


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    """Cancelar job en ejecución."""
    jm = _get_job_manager(request)
    success = jm.cancel_job(job_id)
    if not success:
        raise HTTPException(status_code=400, detail="No se pudo cancelar (ya terminado o no existe)")
    return {"status": "cancelled", "job_id": job_id}
