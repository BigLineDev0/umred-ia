from transformers import pipeline

pipe = pipeline(
    "text-generation",
    model="Qwen/Qwen2.5-1.5B-Instruct",
    device_map="auto",
)


messages = [
    {
        "role": "system",
        "content": "Tu es un assistant utile."
    },
    {
        "role": "user",
        "content": "Bonjour, présente-toi en une phrase."
    }
]


result = pipe(
    messages,
    max_new_tokens=100,
    do_sample=False,
)


print(result)