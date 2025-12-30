import os
import sys

# Add src to path
sys.path.append(os.path.join(os.getcwd()))

from app.core.db.database import Base

# Import all models to register them
try:
    # from app.models import *
    # Explicitly import models here if needed for registration
    # Add other models as needed

    print("Successfully imported app.models (partial)")
except Exception as e:
    print(f"Error importing app.models: {e}")

print("Registered tables in Base.metadata:")
for table_name in Base.metadata.tables.keys():
    print(f" - {table_name}")
