#!/usr/bin/env python3
"""
Verificación comprehensiva del dataset merged_v10_together_train.jsonl
Revisa 7 criterios de calidad y reporta resultados detallados.
"""

import json
import re
import sys

DATASET_PATH = "/Users/marianomorales/Downloads/fine-tuning/inference/datasets/merged_v10_together_train.jsonl"

def extract_visible_text(content: str) -> str:
    """Remove <tool_call>...</tool_call> blocks and return visible text only."""
    # Remove tool_call blocks (including multiline)
    cleaned = re.sub(r'<tool_call>.*?</tool_call>', '', content, flags=re.DOTALL)
    return cleaned.strip()

def get_first_assistant_visible_text(messages: list) -> tuple:
    """
    Find the first assistant message whose visible text (outside tool_call tags)
    is non-empty. Returns (index, visible_text) or (None, None).
    """
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant":
            visible = extract_visible_text(msg.get("content", ""))
            if visible:
                return i, visible
    return None, None

def main():
    # Load dataset
    conversations = []
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                conv = json.loads(line)
                conversations.append(conv)
            except json.JSONDecodeError as e:
                print(f"ERROR: JSON parse error at line {line_num}: {e}")
                sys.exit(1)

    total = len(conversations)
    print(f"Dataset loaded: {total} conversations")
    print("=" * 70)

    # =========================================================================
    # CHECK 1: Identity - First visible assistant text mentions "Mariana"
    # =========================================================================
    c1_pass = 0
    c1_fail = 0
    c1_fail_indices = []
    c1_no_assistant = 0

    for idx, conv in enumerate(conversations):
        msgs = conv.get("messages", [])
        _, visible = get_first_assistant_visible_text(msgs)
        if visible is None:
            c1_no_assistant += 1
            c1_fail += 1
            c1_fail_indices.append(idx)
        elif "mariana" in visible.lower():
            c1_pass += 1
        else:
            c1_fail += 1
            c1_fail_indices.append(idx)

    # =========================================================================
    # CHECK 2: No TREFABOT in any message
    # =========================================================================
    c2_violations = 0
    c2_fail_indices = []

    for idx, conv in enumerate(conversations):
        msgs = conv.get("messages", [])
        found = False
        for msg in msgs:
            content = msg.get("content", "")
            if "TREFABOT" in content:
                found = True
                break
        if found:
            c2_violations += 1
            c2_fail_indices.append(idx)

    # =========================================================================
    # CHECK 3: Double Hola - consecutive assistant messages both containing "Hola"
    # =========================================================================
    c3_violations = 0
    c3_fail_indices = []

    for idx, conv in enumerate(conversations):
        msgs = conv.get("messages", [])
        found_violation = False
        for i in range(len(msgs) - 1):
            if msgs[i].get("role") == "assistant" and msgs[i + 1].get("role") == "assistant":
                # Consecutive assistant messages (no user between them)
                text_a = extract_visible_text(msgs[i].get("content", ""))
                text_b = extract_visible_text(msgs[i + 1].get("content", ""))
                if text_a and text_b:
                    has_hola_a = "hola" in text_a.lower()
                    has_hola_b = "hola" in text_b.lower()
                    if has_hola_a and has_hola_b:
                        found_violation = True
                        break
        if found_violation:
            c3_violations += 1
            c3_fail_indices.append(idx)

    # =========================================================================
    # CHECK 4: Endings - every conversation ends with assistant message
    # =========================================================================
    c4_violations = 0
    c4_fail_indices = []

    for idx, conv in enumerate(conversations):
        msgs = conv.get("messages", [])
        if not msgs:
            c4_violations += 1
            c4_fail_indices.append(idx)
        elif msgs[-1].get("role") != "assistant":
            c4_violations += 1
            c4_fail_indices.append(idx)

    # =========================================================================
    # CHECK 5: Role consistency - no user messages with <tool_response>
    # =========================================================================
    c5_violations = 0
    c5_fail_indices = []

    for idx, conv in enumerate(conversations):
        msgs = conv.get("messages", [])
        found = False
        for msg in msgs:
            if msg.get("role") == "user" and "<tool_response>" in msg.get("content", ""):
                found = True
                break
        if found:
            c5_violations += 1
            c5_fail_indices.append(idx)

    # =========================================================================
    # CHECK 6: Tool call sequence - every <tool_call> in assistant should be
    #           followed by role="tool" before next user or non-tool-call assistant
    # =========================================================================
    c6_violations = 0
    c6_fail_indices = []

    for idx, conv in enumerate(conversations):
        msgs = conv.get("messages", [])
        found_violation = False
        for i, msg in enumerate(msgs):
            if msg.get("role") == "assistant" and "<tool_call>" in msg.get("content", ""):
                # This assistant message has a tool_call, check what follows
                if i + 1 >= len(msgs):
                    # End of conversation after tool_call - violation
                    found_violation = True
                    break
                next_msg = msgs[i + 1]
                if next_msg.get("role") != "tool":
                    found_violation = True
                    break
        if found_violation:
            c6_violations += 1
            c6_fail_indices.append(idx)

    # =========================================================================
    # CHECK 7: Enthusiastic greeting - % of first assistant visible text
    #           starting with "¡Hola!"
    # =========================================================================
    c7_count = 0
    c7_total_with_text = 0

    for idx, conv in enumerate(conversations):
        msgs = conv.get("messages", [])
        _, visible = get_first_assistant_visible_text(msgs)
        if visible is not None:
            c7_total_with_text += 1
            if visible.startswith("¡Hola!"):
                c7_count += 1

    c7_pct = (c7_count / c7_total_with_text * 100) if c7_total_with_text > 0 else 0

    # =========================================================================
    # REPORT
    # =========================================================================
    print()
    print(f"{'CHECK':<45} {'RESULT':<10} {'DETAIL'}")
    print("-" * 90)

    # Check 1
    c1_status = "PASS" if c1_fail == 0 else "FAIL"
    print(f"{'1. Identity (Mariana in 1st asst text)':<45} {c1_status:<10} {c1_pass} pass, {c1_fail} fail (no-asst: {c1_no_assistant})")

    # Check 2
    c2_status = "PASS" if c2_violations == 0 else "FAIL"
    print(f"{'2. No TREFABOT':<45} {c2_status:<10} {c2_violations} violations")

    # Check 3
    c3_status = "PASS" if c3_violations == 0 else "FAIL"
    print(f"{'3. Double Hola (consecutive asst msgs)':<45} {c3_status:<10} {c3_violations} violations")

    # Check 4
    c4_status = "PASS" if c4_violations == 0 else "FAIL"
    print(f"{'4. Ends with assistant message':<45} {c4_status:<10} {c4_violations} violations")

    # Check 5
    c5_status = "PASS" if c5_violations == 0 else "FAIL"
    print(f"{'5. Role consistency (no tool_response in user)':<45} {c5_status:<10} {c5_violations} violations")

    # Check 6
    c6_status = "PASS" if c6_violations == 0 else "FAIL"
    print(f"{'6. Tool call sequence (tool_call -> tool msg)':<45} {c6_status:<10} {c6_violations} violations")

    # Check 7
    c7_status = "INFO"
    print(f"{'7. Enthusiastic greeting (starts ¡Hola!)':<45} {c7_status:<10} {c7_count}/{c7_total_with_text} ({c7_pct:.1f}%)")

    print("-" * 90)

    # Overall
    all_checks = [c1_fail == 0, c2_violations == 0, c3_violations == 0,
                  c4_violations == 0, c5_violations == 0, c6_violations == 0]
    all_passed = all(all_checks)

    print()
    if all_passed:
        print("=" * 70)
        print("  ALL CHECKS PASSED")
        print("=" * 70)
    else:
        print("=" * 70)
        print("  SOME CHECKS FAILED - Details below:")
        print("=" * 70)

        if c1_fail > 0:
            print(f"\n  Check 1 failures (no 'Mariana' in first visible assistant text):")
            sample = c1_fail_indices[:20]
            print(f"    Indices (first {len(sample)} of {len(c1_fail_indices)}): {sample}")
            # Show first few examples
            for ci in c1_fail_indices[:5]:
                msgs = conversations[ci].get("messages", [])
                _, visible = get_first_assistant_visible_text(msgs)
                snippet = (visible[:120] + "...") if visible and len(visible) > 120 else visible
                print(f"    [{ci}] First visible text: {snippet!r}")

        if c2_violations > 0:
            print(f"\n  Check 2 failures (TREFABOT found):")
            sample = c2_fail_indices[:20]
            print(f"    Indices (first {len(sample)} of {len(c2_fail_indices)}): {sample}")

        if c3_violations > 0:
            print(f"\n  Check 3 failures (Double Hola):")
            sample = c3_fail_indices[:20]
            print(f"    Indices (first {len(sample)} of {len(c3_fail_indices)}): {sample}")
            for ci in c3_fail_indices[:5]:
                msgs = conversations[ci].get("messages", [])
                for i in range(len(msgs) - 1):
                    if msgs[i].get("role") == "assistant" and msgs[i + 1].get("role") == "assistant":
                        text_a = extract_visible_text(msgs[i].get("content", ""))
                        text_b = extract_visible_text(msgs[i + 1].get("content", ""))
                        if text_a and text_b and "hola" in text_a.lower() and "hola" in text_b.lower():
                            snip_a = (text_a[:80] + "...") if len(text_a) > 80 else text_a
                            snip_b = (text_b[:80] + "...") if len(text_b) > 80 else text_b
                            print(f"    [{ci}] msg[{i}]: {snip_a!r}")
                            print(f"    [{ci}] msg[{i+1}]: {snip_b!r}")
                            break

        if c4_violations > 0:
            print(f"\n  Check 4 failures (not ending with assistant):")
            sample = c4_fail_indices[:20]
            print(f"    Indices (first {len(sample)} of {len(c4_fail_indices)}): {sample}")
            for ci in c4_fail_indices[:5]:
                msgs = conversations[ci].get("messages", [])
                if msgs:
                    last = msgs[-1]
                    print(f"    [{ci}] Last message role: {last.get('role')}")

        if c5_violations > 0:
            print(f"\n  Check 5 failures (tool_response in user message):")
            sample = c5_fail_indices[:20]
            print(f"    Indices (first {len(sample)} of {len(c5_fail_indices)}): {sample}")

        if c6_violations > 0:
            print(f"\n  Check 6 failures (tool_call not followed by tool message):")
            sample = c6_fail_indices[:20]
            print(f"    Indices (first {len(sample)} of {len(c6_fail_indices)}): {sample}")
            for ci in c6_fail_indices[:10]:
                msgs = conversations[ci].get("messages", [])
                for i, msg in enumerate(msgs):
                    if msg.get("role") == "assistant" and "<tool_call>" in msg.get("content", ""):
                        if i + 1 >= len(msgs):
                            print(f"    [{ci}] msg[{i}] has tool_call but is last message")
                            break
                        next_msg = msgs[i + 1]
                        if next_msg.get("role") != "tool":
                            next_role = next_msg.get("role")
                            next_snippet = next_msg.get("content", "")[:120].replace("\n", " ")
                            print(f"    [{ci}] msg[{i}] (assistant+tool_call) -> msg[{i+1}] role={next_role}: {next_snippet!r}")
                            break

    print()


if __name__ == "__main__":
    main()
