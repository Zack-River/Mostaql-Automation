import re

with open("ai/prompts.py", "r", encoding="utf-8") as f:
    content = f.read()

new_output_format = """# OUTPUT FORMAT

أخرج النتيجة بهذا التنسيق القصير والمباشر باللغة العربية فقط. لا تضع جدول تفاصيل التقييم. لا تضع تحليلاً طويلاً. فقط هذه النقاط بشكل واضح:

## تقييم المشروع
**XX/100**

## القرار
**تقديم بقوة / تقديم / تقديم بشروط / فرصة ضعيفة / تجاهل**

## التسعير
**السعر العادل:** $XX
**سعر التقديم (المقترح):** $XX

## المدة
**المدة الواقعية:** X أيام
**مدة التقديم (المقترحة):** X أيام

## السبب باختصار
جملة واحدة توضح سبب القرار.

---

# CRITICAL RULES"""

content = re.sub(r"# OUTPUT FORMAT.*?# CRITICAL RULES", new_output_format, content, flags=re.DOTALL)

with open("ai/prompts.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Prompt format updated to Arabic.")
