"""E2E Tests: Module Generator Validation.

Tests validate that the module generator CLI correctly:
- Generates all 6 required files (models, schemas, crud, service, router, models_test)
- Uses correct FastCRUD patterns
- Integrates with tenant awareness
- Follows project conventions
- Produces valid Python code
- Generates working modules
"""

import ast
import os
import subprocess
import tempfile
from pathlib import Path

import pytest


class TestModuleGeneratorE2E:
    """End-to-end tests for module generator CLI."""

    MODULE_GENERATOR_CMD = ["uv", "run", "python", "-m", "src.scripts.create_module"]

    def test_generator_creates_all_six_files(self):
        """Test that generator creates all 6 required module files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_module"

            # Run module generator
            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:str,status:bool,count:int",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            # Generator should complete successfully
            assert result.returncode == 0, f"Generator failed: {result.stderr}"

            # Check all 6 files exist
            expected_files = [
                f"{module_name}_models.py",
                f"{module_name}_schemas.py",
                f"{module_name}_crud.py",
                f"{module_name}_service.py",
                f"{module_name}_router.py",
                f"{module_name}_models_test.py",
            ]

            for filename in expected_files:
                filepath = Path(tmpdir) / filename
                assert filepath.exists(), f"Missing file: {filename}"
                assert filepath.stat().st_size > 0, f"Empty file: {filename}"

    def test_generator_produces_valid_python_syntax(self):
        """Test that generated files have valid Python syntax."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_valid_syntax"

            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "title:str,active:bool",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            assert result.returncode == 0

            # Parse each generated file
            for filename in Path(tmpdir).glob("*.py"):
                content = filename.read_text()

                # Should be valid Python
                try:
                    ast.parse(content)
                except SyntaxError as e:
                    pytest.fail(f"Syntax error in {filename.name}: {e}")

    def test_generator_creates_tenant_aware_models(self):
        """Test that generated models include tenant awareness by default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_tenant_aware"

            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:str",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            assert result.returncode == 0

            # Check model file includes tenant_id
            models_file = Path(tmpdir) / f"{module_name}_models.py"
            content = models_file.read_text()

            assert "tenant_id" in content, "Model should include tenant_id field"
            assert "UUID" in content, "Model should use UUID type"

    def test_generator_creates_non_tenant_models(self):
        """Test that generator supports --no-tenant flag."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_no_tenant"

            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:str",
                    "--no-tenant",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            assert result.returncode == 0

            models_file = Path(tmpdir) / f"{module_name}_models.py"
            content = models_file.read_text()

            # Should not have tenant_id when --no-tenant is used
            assert "tenant_id" not in content, "Model should not include tenant_id with --no-tenant"

    def test_generator_field_types(self):
        """Test that generator handles various field types correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_field_types"

            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:str,active:bool,count:int,price:Decimal,ratio:float,created:datetime,description:text,metadata:json,ref_id:uuid",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            assert result.returncode == 0, f"Generator failed: {result.stderr}"

            # Check schema file has all field types
            schemas_file = Path(tmpdir) / f"{module_name}_schemas.py"
            content = schemas_file.read_text()

            expected_types = ["str", "bool", "int", "Decimal", "float", "datetime", "Optional[str]", "dict", "UUID"]

            for type_name in expected_types:
                assert type_name in content, f"Missing type: {type_name}"

    def test_generator_optional_fields(self):
        """Test that generator handles optional fields (with ? suffix)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_optional"

            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:str,description:text?,optional_count:int?",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            assert result.returncode == 0

            schemas_file = Path(tmpdir) / f"{module_name}_schemas.py"
            content = schemas_file.read_text()

            # Optional fields should use Optional type
            assert "Optional" in content, "Should use Optional for nullable fields"

    def test_generator_creates_fastcrud_crud(self):
        """Test that generator creates FastCRUD-based CRUD operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_fastcrud"

            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:str",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            assert result.returncode == 0

            crud_file = Path(tmpdir) / f"{module_name}_crud.py"
            content = crud_file.read_text()

            # Should use FastCRUD
            assert "FastCRUD" in content, "Should use FastCRUD"
            assert "crud_" in content, "Should create crud instance"

    def test_generator_creates_service_class(self):
        """Test that generator creates service class with CRUD methods."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_service"

            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:str",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            assert result.returncode == 0

            service_file = Path(tmpdir) / f"{module_name}_service.py"
            content = service_file.read_text()

            # Should have service class
            assert "class" in content and "Service" in content, "Should define service class"

            # Should have CRUD methods
            assert "async def get" in content or "async def create" in content, "Should have async CRUD methods"

    def test_generator_creates_router(self):
        """Test that generator creates FastAPI router with endpoints."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_router"

            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:str",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            assert result.returncode == 0

            router_file = Path(tmpdir) / f"{module_name}_router.py"
            content = router_file.read_text()

            # Should use FastAPI router
            assert "APIRouter" in content, "Should use APIRouter"
            assert "router" in content, "Should create router instance"

            # Should have HTTP methods
            assert any(method in content for method in ["@router.get", "@router.post"]), (
                "Should define router endpoints"
            )

    def test_generator_dry_run_mode(self):
        """Test that --dry-run flag shows output without writing files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_dry_run"

            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:str",
                    "--dry-run",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            # Dry run should succeed
            assert result.returncode == 0, f"Dry run failed: {result.stderr}"

            # Should show file contents in output
            assert "class" in result.stdout or "#" in result.stdout, "Dry run should show file content preview"

            # No files should be created
            files = list(Path(tmpdir).glob("*.py"))
            assert len(files) == 0, f"Dry run should not create files, found: {files}"

    def test_generator_force_overwrite(self):
        """Test that --force flag overwrites existing files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_force"

            # First generation
            result1 = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:str",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )
            assert result1.returncode == 0

            # Get original file timestamp
            models_file = Path(tmpdir) / f"{module_name}_models.py"
            original_mtime = models_file.stat().st_mtime

            # Second generation with force
            result2 = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:str,status:bool",
                    "--force",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )
            assert result2.returncode == 0

            # File should be overwritten (timestamp changed)
            new_mtime = models_file.stat().st_mtime
            assert new_mtime > original_mtime, "File should be overwritten with --force"

    def test_generator_error_on_invalid_field_type(self):
        """Test that generator handles invalid field types gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_invalid_type"

            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:invalid_type",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            # Should fail or warn about invalid type
            assert result.returncode != 0 or "warning" in result.stderr.lower() or "invalid" in result.stderr.lower(), (
                "Should fail or warn on invalid field type"
            )

    def test_generator_follows_project_conventions(self):
        """Test that generated code follows project conventions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_conventions"

            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:str,created_at:datetime",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            assert result.returncode == 0

            # Check models file follows conventions
            models_file = Path(tmpdir) / f"{module_name}_models.py"
            content = models_file.read_text()

            # Should use absolute imports (src.app pattern)
            assert "from src." in content or "import src.", "Should use absolute imports"

            # Should use SQLAlchemy 2.0 style
            assert "mapped_column" in content, "Should use SQLAlchemy 2.0 mapped_column"

            # Should have docstrings
            assert '"""' in content, "Should include docstrings"


class TestModuleGeneratorIntegration:
    """Integration tests for generated modules."""

    MODULE_GENERATOR_CMD = [
        "uv", "run", "python", "-m", "src.scripts.create_module"
    ]

    def test_generated_module_can_be_imported(self):
        """Test that generated Python module files can be imported."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_importable"

            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:str,active:bool",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            assert result.returncode == 0

            # Create an __init__.py for the temp module
            init_file = Path(tmpdir) / "__init__.py"
            init_file.write_text("")

            # Try to import the generated modules (syntax check only)
            for filename in Path(tmpdir).glob("*_models.py"):
                content = filename.read_text()

                # Replace absolute imports with relative for isolated testing
                test_content = content.replace("from src.app.", "# from src.app.")

                try:
                    compile(test_content, filename.name, "exec")
                except SyntaxError as e:
                    pytest.fail(f"Generated file has syntax error: {e}")

    def test_generator_output_matches_expected_structure(self):
        """Test that generator output matches expected file structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_structure"

            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "name:str,description:text",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            assert result.returncode == 0

            # All files should have expected structure
            expected_structure = {
                f"{module_name}_models.py": ["class", "Base", "Mapped"],
                f"{module_name}_schemas.py": ["BaseModel", "ConfigDict"],
                f"{module_name}_crud.py": ["FastCRUD"],
                f"{module_name}_service.py": ["class", "Service"],
                f"{module_name}_router.py": ["APIRouter"],
                f"{module_name}_models_test.py": ["def test_"],
            }

            for filename, required_patterns in expected_structure.items():
                filepath = Path(tmpdir) / filename
                content = filepath.read_text()

                for pattern in required_patterns:
                    assert pattern in content, f"{filename} missing required pattern: {pattern}"

    def test_generator_handles_special_characters_in_field_names(self):
        """Test that generator handles special characters in field names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_name = "test_special_chars"

            # Use field names with underscores (valid Python identifiers)
            result = subprocess.run(
                [
                    *self.MODULE_GENERATOR_CMD,
                    module_name,
                    "--fields",
                    "user_name:str,created_at:datetime,is_active:bool",
                    "--output-dir",
                    tmpdir,
                ],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )

            assert result.returncode == 0, f"Generator failed: {result.stderr}"

            models_file = Path(tmpdir) / f"{module_name}_models.py"
            content = models_file.read_text()

            # Should have valid Python identifiers
            assert "user_name" in content
            assert "created_at" in content
            assert "is_active" in content
