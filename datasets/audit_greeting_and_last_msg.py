import json
import re
from collections import Counter

base = "/Users/marianomorales/Downloads/fine-tuning/inference/datasets"

files = [
    "merged_v10_together_train.jsonl",
    "merged_v10_together_eval.jsonl",
]

# Enthusiastic greeting patterns (Mariana's style)
GREETING_PATTERNS = [
    r'¡[Hh]ola',
    r'[Hh]ola[,!]',
    r'¡[Bb]uen(?:os días|as tardes|as noches|as)',
    r'[Bb]uen(?:os días|as tardes|as noches|as)[,!]',
    r'¡[Qq]ué (?:gusto|tal|onda)',
    r'[Bb]ienvenid[oa@]',
    r'¡[Bb]ienvenid',
    r'[Gg]racias por (?:contactar|comunicarte|escribir)',
    r'¡[Gg]racias por',
    r'[Ee]ncantad[oa]',
    r'[Cc]on gusto',
    r'[Cc]on mucho gusto',
    r'¡[Cc]laro',
    r'[Ss]oy Mariana',
    r'[Mm]e llamo Mariana',
    r'[Mm]ariana.*(?:asistente|asesora|ayudar)',
    r'¡[Ee]xcelente',
]

# Cold/robotic patterns that indicate lack of enthusiasm
COLD_PATTERNS = [
    r'^(?:Entendido|De acuerdo|Ok|Okay|Bien)[.,]',
    r'^(?:Voy a|Déjame|Permíteme)\s+(?:buscar|revisar)',
]

for fname in files:
    fpath = f"{base}/{fname}"
    print(f"\n{'='*70}")
    print(f"  Auditing: {fname}")
    print(f"{'='*70}")

    total = 0
    no_greeting = []
    cold_greeting = []
    user_last = []
    tool_last = []
    system_last = []
    greeting_types = Counter()
    last_role_counts = Counter()

    with open(fpath) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            msgs = obj.get("messages", [])
            total += 1

            if not msgs:
                continue

            # --- Check 1: First assistant message has enthusiastic greeting ---
            first_assistant = None
            first_assistant_idx = None
            for i, msg in enumerate(msgs):
                if msg.get("role") == "assistant":
                    first_assistant = str(msg.get("content", "") or "")
                    first_assistant_idx = i
                    break

            if first_assistant is not None:
                has_greeting = False
                for pattern in GREETING_PATTERNS:
                    if re.search(pattern, first_assistant):
                        has_greeting = True
                        greeting_types[pattern] += 1
                        break

                if not has_greeting:
                    # Check if it's cold/robotic
                    is_cold = any(re.search(p, first_assistant) for p in COLD_PATTERNS)

                    # Get the first user message for context
                    first_user = ""
                    for msg in msgs:
                        if msg.get("role") == "user":
                            first_user = str(msg.get("content", "") or "")[:80]
                            break

                    if is_cold:
                        cold_greeting.append({
                            "line": line_num,
                            "first_user": first_user,
                            "first_assistant": first_assistant[:150],
                        })
                    else:
                        no_greeting.append({
                            "line": line_num,
                            "first_user": first_user,
                            "first_assistant": first_assistant[:150],
                        })

            # --- Check 2: Last message is from assistant ---
            last_msg = msgs[-1]
            last_role = last_msg.get("role", "unknown")
            last_role_counts[last_role] += 1

            if last_role == "user":
                last_content = str(last_msg.get("content", "") or "")[:100]
                user_last.append({
                    "line": line_num,
                    "last_user_msg": last_content,
                })
            elif last_role == "tool":
                tool_last.append({
                    "line": line_num,
                    "last_tool_msg": str(last_msg.get("content", "") or "")[:80],
                })
            elif last_role == "system":
                system_last.append({"line": line_num})

    # Report
    print(f"\n  Total conversations: {total}")

    print(f"\n  --- GREETING CHECK ---")
    print(f"  With enthusiastic greeting: {total - len(no_greeting) - len(cold_greeting)}")
    print(f"  Without greeting: {len(no_greeting)}")
    print(f"  Cold/robotic greeting: {len(cold_greeting)}")

    if no_greeting:
        print(f"\n  NO GREETING ({len(no_greeting)} conversations):")
        for e in no_greeting[:20]:
            print(f"    L{e['line']}: User: \"{e['first_user'][:60]}\"")
            print(f"           Asst: \"{e['first_assistant'][:100]}\"")
        no_greeting_lines = sorted(e['line'] for e in no_greeting)
        print(f"\n    ALL no-greeting lines ({len(no_greeting_lines)}): {no_greeting_lines}")

    if cold_greeting:
        print(f"\n  COLD GREETING ({len(cold_greeting)} conversations):")
        for e in cold_greeting[:10]:
            print(f"    L{e['line']}: User: \"{e['first_user'][:60]}\"")
            print(f"           Asst: \"{e['first_assistant'][:100]}\"")
        cold_lines = sorted(e['line'] for e in cold_greeting)
        print(f"\n    ALL cold-greeting lines ({len(cold_lines)}): {cold_lines}")

    print(f"\n  --- LAST MESSAGE CHECK ---")
    print(f"  Last message role distribution:")
    for role, count in last_role_counts.most_common():
        pct = 100 * count / total
        status = "OK" if role == "assistant" else "PROBLEM"
        print(f"    {role:<15} {count:>5} ({pct:.1f}%)  [{status}]")

    if user_last:
        print(f"\n  USER IS LAST ({len(user_last)} conversations):")
        for e in user_last[:15]:
            print(f"    L{e['line']}: \"{e['last_user_msg'][:80]}\"")
        user_last_lines = sorted(e['line'] for e in user_last)
        print(f"\n    ALL user-last lines ({len(user_last_lines)}): {user_last_lines}")

    if tool_last:
        print(f"\n  TOOL IS LAST ({len(tool_last)} conversations):")
        for e in tool_last[:10]:
            print(f"    L{e['line']}: \"{e['last_tool_msg'][:60]}\"")
        tool_last_lines = sorted(e['line'] for e in tool_last)
        print(f"\n    ALL tool-last lines ({len(tool_last_lines)}): {tool_last_lines}")

print(f"\n\n{'='*70}")
print("COMBINED SUMMARY")
print(f"{'='*70}")
