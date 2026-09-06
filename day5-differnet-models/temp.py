import math

# Raw scores (logits) a model might assign to four candidate
# next tokens for the prompt "The sky over the desert was ..."
logits = {"blue": 5.0, "grey": 4.2, "vast": 3.1, "banana": 0.5}

def apply_temperature(logits, t):
    scaled = {tok: s / t for tok, s in logits.items()}  # the temperature step
    total = sum(math.exp(s) for s in scaled.values())
    return {tok: math.exp(s) / total for tok, s in scaled.items()}  # softmax

for t in (0.2, 0.7, 1.0, 1.5):
    probs = apply_temperature(logits, t)
    row = "  ".join(f"{tok} {p:6.1%}" for tok, p in probs.items())
    print(f"T={t}:  {row}")
