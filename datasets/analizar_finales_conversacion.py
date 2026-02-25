#!/usr/bin/env python3
"""
Análisis de finales de conversación en el dataset de entrenamiento.
Regla crítica: Mariana (assistant) SIEMPRE debe ser quien termina la conversación.
"""

import json
import sys
from collections import Counter

INPUT_FILE = "/Users/marianomorales/Downloads/fine-tuning/inference/datasets/merged_v10_together_train.jsonl"

def truncate(text, max_len=100):
    """Trunca texto a max_len caracteres."""
    if text is None:
        return "<None>"
    text = text.replace("\n", "\\n")
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text

def has_tool_call(content):
    """Verifica si el contenido tiene un <tool_call>."""
    if content is None:
        return False
    return "<tool_call>" in content

def has_natural_text(content):
    """Verifica si el contenido tiene texto natural (no solo tool_call)."""
    if content is None:
        return False
    # Remove tool_call blocks and check if there's remaining text
    import re
    cleaned = re.sub(r'<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL).strip()
    return len(cleaned) > 0

def analyze():
    conversations = []
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        for line_idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                conversations.append((line_idx, obj))
            except json.JSONDecodeError as e:
                print(f"  ERROR JSON en línea {line_idx}: {e}")

    total = len(conversations)
    print("=" * 80)
    print(f"ANÁLISIS DE FINALES DE CONVERSACIÓN")
    print(f"Archivo: {INPUT_FILE}")
    print(f"Total de conversaciones: {total}")
    print("=" * 80)

    # =========================================================================
    # CHECK 1: Rol del último mensaje
    # =========================================================================
    print("\n" + "=" * 80)
    print("CHECK 1: ROL DEL ÚLTIMO MENSAJE")
    print("=" * 80)

    last_role_counter = Counter()
    problems_check1 = []

    for line_idx, obj in conversations:
        messages = obj.get("messages", [])
        if not messages:
            last_role_counter["<EMPTY>"] += 1
            problems_check1.append((line_idx, messages, "<EMPTY>"))
            continue

        last_msg = messages[-1]
        role = last_msg.get("role", "<UNKNOWN>")
        last_role_counter[role] += 1

        if role != "assistant":
            problems_check1.append((line_idx, messages, role))

    print(f"\nDistribución de roles del último mensaje:")
    print(f"{'Rol':<15} {'Cantidad':>10} {'Porcentaje':>10}")
    print("-" * 37)
    for role, count in sorted(last_role_counter.items(), key=lambda x: -x[1]):
        pct = (count / total) * 100
        marker = " ✓" if role == "assistant" else " ← PROBLEMA"
        print(f"{role:<15} {count:>10} {pct:>9.1f}%{marker}")

    if problems_check1:
        print(f"\n--- Conversaciones donde el último mensaje NO es 'assistant' ({len(problems_check1)}) ---")
        for line_idx, messages, role in problems_check1[:50]:
            print(f"\n  [Línea {line_idx}] Último rol: {role}")
            # Show last 3 messages
            show_msgs = messages[-3:] if len(messages) >= 3 else messages
            for i, msg in enumerate(show_msgs):
                offset = len(messages) - len(show_msgs) + i
                content = msg.get("content", "")
                print(f"    msg[{offset}] role={msg.get('role')}: {truncate(content)}")
        if len(problems_check1) > 50:
            print(f"\n  ... y {len(problems_check1) - 50} más")
    else:
        print("\n  Todas las conversaciones terminan con role='assistant'.")

    # =========================================================================
    # CHECK 2: Calidad del final
    # =========================================================================
    print("\n" + "=" * 80)
    print("CHECK 2: CALIDAD DEL FINAL (solo conversaciones que terminan con assistant)")
    print("=" * 80)

    problems_only_tool_call = []
    problems_short_ending = []
    good_endings = 0

    for line_idx, obj in conversations:
        messages = obj.get("messages", [])
        if not messages:
            continue

        last_msg = messages[-1]
        if last_msg.get("role") != "assistant":
            continue

        content = last_msg.get("content", "") or ""

        # Check: only tool_call, no natural text
        if has_tool_call(content) and not has_natural_text(content):
            problems_only_tool_call.append((line_idx, messages))
            continue

        # Check: very short ending
        # Strip tool_call content before checking length
        import re
        clean_content = re.sub(r'<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL).strip()
        if len(clean_content) < 20:
            problems_short_ending.append((line_idx, messages, clean_content))
            continue

        good_endings += 1

    assistant_endings = total - len(problems_check1)
    print(f"\nConversaciones que terminan con assistant: {assistant_endings}")
    print(f"  Buenos finales:                 {good_endings} ({good_endings/total*100:.1f}%)")
    print(f"  Solo tool_call (sin texto):     {len(problems_only_tool_call)} ({len(problems_only_tool_call)/total*100:.1f}%)")
    print(f"  Final muy corto (<20 chars):    {len(problems_short_ending)} ({len(problems_short_ending)/total*100:.1f}%)")

    if problems_only_tool_call:
        print(f"\n--- Conversaciones con final SOLO tool_call ({len(problems_only_tool_call)}) ---")
        for line_idx, messages in problems_only_tool_call[:30]:
            print(f"\n  [Línea {line_idx}]")
            show_msgs = messages[-3:] if len(messages) >= 3 else messages
            for i, msg in enumerate(show_msgs):
                offset = len(messages) - len(show_msgs) + i
                content = msg.get("content", "")
                print(f"    msg[{offset}] role={msg.get('role')}: {truncate(content)}")
        if len(problems_only_tool_call) > 30:
            print(f"\n  ... y {len(problems_only_tool_call) - 30} más")

    if problems_short_ending:
        print(f"\n--- Conversaciones con final MUY CORTO ({len(problems_short_ending)}) ---")
        for line_idx, messages, clean_content in problems_short_ending[:30]:
            print(f"\n  [Línea {line_idx}] Texto final: \"{clean_content}\"")
            show_msgs = messages[-3:] if len(messages) >= 3 else messages
            for i, msg in enumerate(show_msgs):
                offset = len(messages) - len(show_msgs) + i
                content = msg.get("content", "")
                print(f"    msg[{offset}] role={msg.get('role')}: {truncate(content)}")
        if len(problems_short_ending) > 30:
            print(f"\n  ... y {len(problems_short_ending) - 30} más")

    # =========================================================================
    # CHECK 3: Tool calls colgantes
    # =========================================================================
    print("\n" + "=" * 80)
    print("CHECK 3: TOOL CALLS COLGANTES")
    print("=" * 80)

    dangling_tool_call = []     # assistant hace tool_call pero no hay tool response después
    dangling_tool_response = [] # hay tool response pero no hay assistant después

    for line_idx, obj in conversations:
        messages = obj.get("messages", [])
        if len(messages) < 2:
            continue

        # Walk messages from the END backwards to find issues
        last_msg = messages[-1]

        # Case A: Last message is assistant with tool_call → no tool response follows
        if last_msg.get("role") == "assistant":
            content = last_msg.get("content", "") or ""
            if has_tool_call(content):
                # Check if this is the pattern: assistant(tool_call) at the end with no tool response
                dangling_tool_call.append((line_idx, messages))

        # Case B: Last message is tool → no assistant follows
        if last_msg.get("role") == "tool":
            dangling_tool_response.append((line_idx, messages))

        # Also check second-to-last: tool response with no assistant follow-up
        # (This is actually caught by Check 1 if last msg is tool, but let's also
        # check deeper patterns where tool_call → tool → END with no assistant summary)
        # We already cover this above.

    # Also scan for internal dangling patterns (not just at the end)
    internal_dangling_tool_call = []
    internal_dangling_tool_response = []

    for line_idx, obj in conversations:
        messages = obj.get("messages", [])
        for i, msg in enumerate(messages):
            if msg.get("role") == "assistant" and has_tool_call(msg.get("content", "") or ""):
                # Expect next message to be tool
                if i + 1 < len(messages):
                    next_msg = messages[i + 1]
                    if next_msg.get("role") not in ("tool", "assistant"):
                        internal_dangling_tool_call.append((line_idx, i, messages))
                        break  # one per conversation

            if msg.get("role") == "tool":
                # Expect next message to be assistant
                if i + 1 < len(messages):
                    next_msg = messages[i + 1]
                    if next_msg.get("role") != "assistant":
                        internal_dangling_tool_response.append((line_idx, i, messages))
                        break  # one per conversation

    print(f"\nAl final de la conversación:")
    print(f"  Assistant con tool_call sin tool response: {len(dangling_tool_call)}")
    print(f"  Tool response sin assistant después:       {len(dangling_tool_response)}")
    print(f"\nPatrones internos (mid-conversation):")
    print(f"  tool_call sin tool response inmediato:     {len(internal_dangling_tool_call)}")
    print(f"  tool response sin assistant después:       {len(internal_dangling_tool_response)}")

    if dangling_tool_call:
        print(f"\n--- Assistant tool_call al final sin tool response ({len(dangling_tool_call)}) ---")
        for line_idx, messages in dangling_tool_call[:30]:
            print(f"\n  [Línea {line_idx}]")
            show_msgs = messages[-3:] if len(messages) >= 3 else messages
            for i, msg in enumerate(show_msgs):
                offset = len(messages) - len(show_msgs) + i
                content = msg.get("content", "")
                print(f"    msg[{offset}] role={msg.get('role')}: {truncate(content)}")
        if len(dangling_tool_call) > 30:
            print(f"\n  ... y {len(dangling_tool_call) - 30} más")

    if dangling_tool_response:
        print(f"\n--- Tool response al final sin assistant ({len(dangling_tool_response)}) ---")
        for line_idx, messages in dangling_tool_response[:30]:
            print(f"\n  [Línea {line_idx}]")
            show_msgs = messages[-3:] if len(messages) >= 3 else messages
            for i, msg in enumerate(show_msgs):
                offset = len(messages) - len(show_msgs) + i
                content = msg.get("content", "")
                print(f"    msg[{offset}] role={msg.get('role')}: {truncate(content)}")
        if len(dangling_tool_response) > 30:
            print(f"\n  ... y {len(dangling_tool_response) - 30} más")

    if internal_dangling_tool_call:
        print(f"\n--- tool_call interno sin tool response ({len(internal_dangling_tool_call)}) ---")
        for line_idx, msg_idx, messages in internal_dangling_tool_call[:20]:
            print(f"\n  [Línea {line_idx}] en msg[{msg_idx}]")
            start = max(0, msg_idx - 1)
            end = min(len(messages), msg_idx + 3)
            for i in range(start, end):
                content = messages[i].get("content", "")
                marker = " <<<" if i == msg_idx else ""
                print(f"    msg[{i}] role={messages[i].get('role')}: {truncate(content)}{marker}")
        if len(internal_dangling_tool_call) > 20:
            print(f"\n  ... y {len(internal_dangling_tool_call) - 20} más")

    if internal_dangling_tool_response:
        print(f"\n--- tool response interno sin assistant después ({len(internal_dangling_tool_response)}) ---")
        for line_idx, msg_idx, messages in internal_dangling_tool_response[:20]:
            print(f"\n  [Línea {line_idx}] en msg[{msg_idx}]")
            start = max(0, msg_idx - 1)
            end = min(len(messages), msg_idx + 3)
            for i in range(start, end):
                content = messages[i].get("content", "")
                marker = " <<<" if i == msg_idx else ""
                print(f"    msg[{i}] role={messages[i].get('role')}: {truncate(content)}{marker}")
        if len(internal_dangling_tool_response) > 20:
            print(f"\n  ... y {len(internal_dangling_tool_response) - 20} más")

    # =========================================================================
    # RESUMEN FINAL
    # =========================================================================
    print("\n" + "=" * 80)
    print("RESUMEN FINAL")
    print("=" * 80)

    total_problems = (
        len(problems_check1) +
        len(problems_only_tool_call) +
        len(problems_short_ending) +
        len(dangling_tool_call) +
        len(dangling_tool_response)
    )

    # Unique problematic conversations (some may appear in multiple checks)
    problem_indices = set()
    for line_idx, _, _ in problems_check1:
        problem_indices.add(line_idx)
    for line_idx, _ in problems_only_tool_call:
        problem_indices.add(line_idx)
    for line_idx, _, _ in problems_short_ending:
        problem_indices.add(line_idx)
    for line_idx, _ in dangling_tool_call:
        problem_indices.add(line_idx)
    for line_idx, _ in dangling_tool_response:
        problem_indices.add(line_idx)

    print(f"\nTotal de conversaciones: {total}")
    print(f"Conversaciones con problemas (únicas): {len(problem_indices)} ({len(problem_indices)/total*100:.1f}%)")
    print(f"Conversaciones limpias:                {total - len(problem_indices)} ({(total - len(problem_indices))/total*100:.1f}%)")
    print(f"\nDesglose de problemas:")
    print(f"  Check 1 - Último mensaje NO es assistant:     {len(problems_check1)}")
    print(f"  Check 2 - Final solo tool_call:               {len(problems_only_tool_call)}")
    print(f"  Check 2 - Final muy corto:                    {len(problems_short_ending)}")
    print(f"  Check 3 - tool_call colgante al final:        {len(dangling_tool_call)}")
    print(f"  Check 3 - tool response sin cierre al final:  {len(dangling_tool_response)}")
    print(f"\nPatrones internos (mid-conversation):")
    print(f"  tool_call sin tool response:                  {len(internal_dangling_tool_call)}")
    print(f"  tool response sin assistant:                  {len(internal_dangling_tool_response)}")

    # Export problematic line indices for potential fix script
    if problem_indices:
        print(f"\nÍndices de líneas problemáticas (para script de corrección):")
        sorted_indices = sorted(problem_indices)
        # Show first 50
        shown = sorted_indices[:50]
        print(f"  {shown}")
        if len(sorted_indices) > 50:
            print(f"  ... y {len(sorted_indices) - 50} más")

    print("\n" + "=" * 80)
    print("FIN DEL ANÁLISIS")
    print("=" * 80)


if __name__ == "__main__":
    analyze()
