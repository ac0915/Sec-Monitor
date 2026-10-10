from secmon.config import get_settings

settings = get_settings()

# Let's print out all the settings your friend's architecture has!
print("--- YOUR SECMON SETTINGS ---")
for key, value in settings.__dict__.items():
    if "telegram" in key.lower() or "chat" in key.lower():
        print(f"{key}: {value}")
