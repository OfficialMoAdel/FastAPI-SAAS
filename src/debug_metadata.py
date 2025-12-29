import os
import sys

# Add src to path
sys.path.append(os.path.join(os.getcwd()))

from app.core.db.database import Base

# Import all models to register them
try:
    from app.models import *

    print("Successfully imported app.models")
except Exception as e:
    print(f"Error importing app.models: {e}")

print("Registered tables in Base.metadata:")
for table_name in Base.metadata.tables.keys():
    print(f" - {table_name}")
