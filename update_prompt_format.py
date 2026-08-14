import re

with open("ai/prompts.py", "r", encoding="utf-8") as f:
    content = f.read()

# Replace the OUTPUT FORMAT section
new_output_format = """# OUTPUT FORMAT

Always return the result in this exact short structure. Do not output the score breakdown table. Do not output long analysis. Just these points clearly formatted.

## PROJECT SCORE
**XX/100**

## DECISION
**STRONG APPLY / APPLY / SELECTIVE APPLY / WEAK OPPORTUNITY / SKIP**

## PRICING
**Fair Price:** $XX
**Force Apply Price (Recommended Bid):** $XX

## DURATION
**Realistic Duration:** X days
**Force Apply Duration (Recommended):** X days

## QUICK REASON
One sentence explaining the decision.

---

# CRITICAL RULES"""

content = re.sub(r"# OUTPUT FORMAT.*?# CRITICAL RULES", new_output_format, content, flags=re.DOTALL)

with open("ai/prompts.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Prompt format updated.")
