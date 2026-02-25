#!/usr/bin/env python3
"""
Audit tool call usage in the training dataset.
Analyzes INPUT arguments and OUTPUT fields for each tool,
flags naming inconsistencies.
"""

import json
import re
from collections import defaultdict, Counter

DATASET_PATH = "/Users/marianomorales/Downloads/fine-tuning/inference/datasets/merged_v10_together_train.jsonl"

# ── Parsing helpers ──────────────────────────────────────────────────────────

def extract_tool_calls(content: str):
    """Extract tool call name + arguments from <tool_call> blocks."""
    results = []
    for match in re.finditer(r'<tool_call>\s*(.*?)\s*</tool_call>', content, re.DOTALL):
        raw = match.group(1).strip()
        try:
            obj = json.loads(raw)
            name = obj.get("name", "__UNKNOWN__")
            args = obj.get("arguments", {})
            results.append((name, args))
        except json.JSONDecodeError:
            results.append(("__PARSE_ERROR__", {"_raw": raw[:200]}))
    return results


def extract_tool_response(content: str):
    """Extract the parsed JSON from a <tool_response> block."""
    match = re.search(r'<tool_response>\s*(.*?)\s*</tool_response>', content, re.DOTALL)
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"__PARSE_ERROR__": raw[:200]}


def deep_field_names(obj, prefix=""):
    """Yield (field_path, value_type_example) for all keys in a dict/list structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            yield full, type(v).__name__
            if isinstance(v, dict):
                yield from deep_field_names(v, full)
            elif isinstance(v, list) and v:
                if isinstance(v[0], dict):
                    yield from deep_field_names(v[0], f"{full}[]")
    elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
        yield from deep_field_names(obj[0], f"{prefix}[]" if prefix else "[]")


# ── Main analysis ────────────────────────────────────────────────────────────

def main():
    tool_input_args = defaultdict(lambda: Counter())
    tool_input_value_samples = defaultdict(lambda: defaultdict(list))
    tool_output_fields = defaultdict(lambda: Counter())
    tool_output_value_types = defaultdict(lambda: defaultdict(Counter))
    tool_call_count = Counter()
    tool_response_count = Counter()
    total_lines = 0
    total_tool_calls = 0
    total_tool_responses = 0

    with open(DATASET_PATH) as f:
        for line_no, line in enumerate(f, 1):
            total_lines += 1
            data = json.loads(line)
            messages = data.get("messages", [])
            last_tool_name = None

            for i, msg in enumerate(messages):
                role = msg.get("role", "")
                content = str(msg.get("content", ""))

                if role == "assistant" and "<tool_call>" in content:
                    calls = extract_tool_calls(content)
                    for name, args in calls:
                        total_tool_calls += 1
                        tool_call_count[name] += 1
                        last_tool_name = name
                        if isinstance(args, dict):
                            for arg_name, arg_val in args.items():
                                tool_input_args[name][arg_name] += 1
                                samples = tool_input_value_samples[name][arg_name]
                                if len(samples) < 10:
                                    samples.append(arg_val)

                if role == "tool":
                    total_tool_responses += 1
                    resp_tool_name = last_tool_name or "__UNKNOWN_TOOL__"
                    resp_obj = extract_tool_response(content)
                    if resp_obj is None:
                        try:
                            resp_obj = json.loads(content)
                        except:
                            resp_obj = None

                    if resp_obj is not None:
                        tool_response_count[resp_tool_name] += 1
                        for field_path, val_type in deep_field_names(resp_obj):
                            tool_output_fields[resp_tool_name][field_path] += 1
                            tool_output_value_types[resp_tool_name][field_path][val_type] += 1

    # ── Report ───────────────────────────────────────────────────────────────
    print("=" * 90)
    print("  TOOL CALL AUDIT REPORT")
    print(f"  Dataset: {DATASET_PATH}")
    print(f"  Total JSONL lines: {total_lines}")
    print(f"  Total tool calls found: {total_tool_calls}")
    print(f"  Total tool responses found: {total_tool_responses}")
    print("=" * 90)

    # Section 1
    print("\n" + "-" * 90)
    print("  1. TOOL CALL FREQUENCY")
    print("-" * 90)
    for name, count in tool_call_count.most_common():
        print(f"    {name:40s}  calls: {count:6d}")

    # Section 2
    print("\n" + "-" * 90)
    print("  2. INPUT ARGUMENTS PER TOOL")
    print("-" * 90)
    for tool_name in sorted(tool_input_args.keys()):
        args = tool_input_args[tool_name]
        print(f"\n  [{tool_name}]  (total calls: {tool_call_count[tool_name]})")
        for arg, count in args.most_common():
            pct = 100.0 * count / tool_call_count[tool_name]
            samples = tool_input_value_samples[tool_name][arg][:3]
            sample_str = ", ".join(repr(s) for s in samples)
            print(f"    {arg:35s}  count: {count:6d}  ({pct:5.1f}%)  samples: [{sample_str}]")

    # Section 3
    print("\n" + "-" * 90)
    print("  3. OUTPUT FIELDS PER TOOL")
    print("-" * 90)
    for tool_name in sorted(tool_output_fields.keys()):
        fields = tool_output_fields[tool_name]
        total_resp = tool_response_count[tool_name]
        print(f"\n  [{tool_name}]  (total responses: {total_resp})")
        for field, count in fields.most_common():
            pct = 100.0 * count / total_resp if total_resp else 0
            types = dict(tool_output_value_types[tool_name][field])
            print(f"    {field:45s}  count: {count:6d}  ({pct:5.1f}%)  types: {types}")

    # Section 4: Inconsistency
    print("\n" + "=" * 90)
    print("  4. INCONSISTENCY ANALYSIS")
    print("=" * 90)

    known_pairs = [
        ("vehiculo_id", "id", "vehicle identification"),
        ("liga_web", "url", "vehicle URL"),
        ("autoano", "año", "vehicle year"),
        ("año_minimo", "ano_minimo", "year min filter (accent)"),
        ("año_maximo", "ano_maximo", "year max filter (accent)"),
        ("precio_minimo", "precio_min", "price min filter"),
        ("precio_maximo", "precio_max", "price max filter"),
    ]

    print("\n  4a. KNOWN SUSPECT FIELD PAIRS")
    print("  " + "-" * 86)
    for field_a, field_b, description in known_pairs:
        print(f"\n    Checking: '{field_a}' vs '{field_b}' ({description})")
        found = False
        for tool_name in sorted(tool_input_args.keys()):
            a_count = tool_input_args[tool_name].get(field_a, 0)
            b_count = tool_input_args[tool_name].get(field_b, 0)
            if a_count > 0 or b_count > 0:
                found = True
                flag = " *** INCONSISTENT ***" if a_count > 0 and b_count > 0 else ""
                print(f"      INPUT  [{tool_name}]: '{field_a}'={a_count}, '{field_b}'={b_count}{flag}")
        for tool_name in sorted(tool_output_fields.keys()):
            a_count = tool_output_fields[tool_name].get(field_a, 0)
            b_count = tool_output_fields[tool_name].get(field_b, 0)
            if a_count > 0 or b_count > 0:
                found = True
                flag = " *** INCONSISTENT ***" if a_count > 0 and b_count > 0 else ""
                print(f"      OUTPUT [{tool_name}]: '{field_a}'={a_count}, '{field_b}'={b_count}{flag}")
            # Check nested paths
            for field_path in tool_output_fields[tool_name]:
                base = field_path.split(".")[-1] if "." in field_path else None
                if base and base in (field_a, field_b) and field_path not in (field_a, field_b):
                    ct = tool_output_fields[tool_name][field_path]
                    print(f"      OUTPUT [{tool_name}]: nested '{field_path}'={ct}")
        if not found:
            print(f"      (neither field found)")

    # 4b: Mixed types
    print("\n  4b. OUTPUT FIELDS WITH MIXED VALUE TYPES")
    print("  " + "-" * 86)
    mixed_found = False
    for tool_name in sorted(tool_output_value_types.keys()):
        for field_path, type_counts in sorted(tool_output_value_types[tool_name].items()):
            if len(type_counts) > 1:
                mixed_found = True
                print(f"    [{tool_name}] {field_path:40s}  types: {dict(type_counts)}")
    if not mixed_found:
        print("    (none found)")

    # 4c: Rare fields
    print("\n  4c. RARELY-APPEARING OUTPUT FIELDS (present in < 50% of responses)")
    print("  " + "-" * 86)
    for tool_name in sorted(tool_output_fields.keys()):
        total_resp = tool_response_count[tool_name]
        if total_resp < 5:
            continue
        rare_fields = []
        for field, count in tool_output_fields[tool_name].most_common():
            pct = 100.0 * count / total_resp
            if pct < 50.0:
                rare_fields.append((field, count, pct))
        if rare_fields:
            print(f"\n    [{tool_name}]  (total responses: {total_resp})")
            for field, count, pct in sorted(rare_fields, key=lambda x: -x[1]):
                print(f"      {field:45s}  count: {count:6d}  ({pct:5.1f}%)")

    # 4d: Cross-tool arg comparison
    print("\n  4d. CROSS-TOOL INPUT ARGUMENT COMPARISON")
    print("  " + "-" * 86)
    all_input_args = defaultdict(list)
    for tool_name, args in tool_input_args.items():
        for arg in args:
            all_input_args[arg].append((tool_name, args[arg]))
    for arg in sorted(all_input_args.keys()):
        tools = all_input_args[arg]
        if len(tools) > 1:
            tool_str = ", ".join(f"{t}({c})" for t, c in tools)
            print(f"    {arg:35s}  used in: {tool_str}")

    # 4e: precio format
    print("\n  4e. PRECIO FORMAT ANALYSIS")
    print("  " + "-" * 86)
    precio_values = defaultdict(list)
    with open(DATASET_PATH) as f:
        for line in f:
            data = json.loads(line)
            messages = data.get("messages", [])
            last_tool = None
            for msg in messages:
                role = msg.get("role", "")
                content = str(msg.get("content", ""))
                if role == "assistant" and "<tool_call>" in content:
                    calls = extract_tool_calls(content)
                    if calls:
                        last_tool = calls[-1][0]
                        for name, args in calls:
                            if "precio" in args:
                                precio_values[f"INPUT:{name}"].append(args["precio"])
                if role == "tool" and last_tool:
                    resp = extract_tool_response(content)
                    if resp and isinstance(resp, dict):
                        if "precio" in resp:
                            precio_values[f"OUTPUT:{last_tool}(top)"].append(resp["precio"])
                        for k, v in resp.items():
                            if isinstance(v, list):
                                for item in v:
                                    if isinstance(item, dict) and "precio" in item:
                                        precio_values[f"OUTPUT:{last_tool}.{k}[]"].append(item["precio"])

    for context, values in sorted(precio_values.items()):
        string_count = sum(1 for v in values if isinstance(v, str))
        numeric_count = sum(1 for v in values if isinstance(v, (int, float)))
        other_count = len(values) - string_count - numeric_count
        print(f"\n    {context}")
        print(f"      Total: {len(values)}, String: {string_count}, Numeric: {numeric_count}, Other: {other_count}")
        if string_count > 0:
            patterns = Counter()
            for v in values:
                if isinstance(v, str):
                    if re.match(r'^\$[\d,]+$', v):
                        patterns["$NNN,NNN"] += 1
                    elif re.match(r'^[\d,]+$', v):
                        patterns["NNN,NNN (no $)"] += 1
                    elif re.match(r'^\d+$', v):
                        patterns["NNNNN (plain digits)"] += 1
                    else:
                        patterns[f"other: {v[:30]}"] += 1
            for pat, cnt in patterns.most_common():
                print(f"        Pattern: {pat:30s}  count: {cnt}")
        if numeric_count > 0:
            nums = [v for v in values if isinstance(v, (int, float))]
            print(f"        Numeric range: {min(nums)} - {max(nums)}")
        samples = values[:5]
        print(f"        Samples: {samples}")

    # 4f: Complete vehiculos[] field inventory
    print("\n  4f. COMPLETE VEHICULOS[] ITEM FIELD INVENTORY (across all tools)")
    print("  " + "-" * 86)
    vehiculo_fields_per_tool = defaultdict(lambda: Counter())
    vehiculo_count_per_tool = Counter()
    with open(DATASET_PATH) as f:
        for line in f:
            data = json.loads(line)
            messages = data.get("messages", [])
            last_tool = None
            for msg in messages:
                role = msg.get("role", "")
                content = str(msg.get("content", ""))
                if role == "assistant" and "<tool_call>" in content:
                    calls = extract_tool_calls(content)
                    if calls:
                        last_tool = calls[-1][0]
                if role == "tool" and last_tool:
                    resp = extract_tool_response(content)
                    if resp and isinstance(resp, dict):
                        for k, v in resp.items():
                            if isinstance(v, list):
                                for item in v:
                                    if isinstance(item, dict):
                                        vehiculo_count_per_tool[last_tool] += 1
                                        for field_name in item.keys():
                                            vehiculo_fields_per_tool[last_tool][field_name] += 1

    for tool_name in sorted(vehiculo_fields_per_tool.keys()):
        total_items = vehiculo_count_per_tool[tool_name]
        fields = vehiculo_fields_per_tool[tool_name]
        print(f"\n    [{tool_name}]  (total list items: {total_items})")
        for field, count in fields.most_common():
            pct = 100.0 * count / total_items
            print(f"      {field:35s}  count: {count:6d}  ({pct:5.1f}%)")

    print("\n" + "=" * 90)
    print("  END OF AUDIT REPORT")
    print("=" * 90)


if __name__ == "__main__":
    main()
