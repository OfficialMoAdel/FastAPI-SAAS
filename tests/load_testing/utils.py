import random
import string
import io
from faker import Faker

fake = Faker()

def generate_random_email() -> str:
    """Generate a random email address."""
    return fake.email()

def generate_random_password() -> str:
    """Generate a secure random password."""
    chars = string.ascii_letters + string.digits + "!@#$%"
    return "".join(random.choice(chars) for _ in range(12))

def generate_random_filename(extension: str = "txt") -> str:
    """Generate a random filename."""
    return f"{fake.word()}_{random.randint(1000, 9999)}.{extension}"

def generate_dummy_file(size_kb: int = 100) -> tuple[str, io.BytesIO, str]:
    """
    Generate a dummy file in memory.
    Returns: (filename, file_content_io, mime_type)
    """
    filename = generate_random_filename("pdf")
    content = fake.binary(length=size_kb * 1024)
    file_io = io.BytesIO(content)
    return filename, file_io, "application/pdf"

def get_random_user_payload() -> dict:
    """Generate payload for creating a new user."""
    password = generate_random_password()
    return {
        "email": generate_random_email(),
        "password": password,
        "full_name": fake.name(),
        "is_active": True
    }
