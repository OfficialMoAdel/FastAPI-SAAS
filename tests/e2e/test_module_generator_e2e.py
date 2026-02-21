"""E2E tests for the module generator CLI."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


class TestModuleGeneratorE2E:
    """E2E tests for module generator CLI."""

    @pytest.fixture(autouse=True)
    def setup_temp_dir(self):
        """Create temp directory for generated modules."""
        self.temp_dir = tempfile.mkdtemp(prefix="test_module_")
        yield
        # Cleanup after test
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def run_generator(
        self, module_name: str, fields: str, output_dir: str | None = None
    ) -> subprocess.CompletedProcess:
        """Run the module generator CLI."""
        cmd = ["uv", "run", "python", "-m", "src.scripts.create_module", module_name, "--fields", fields]
        if output_dir:
            cmd.extend(["--output-dir", output_dir])
        return subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())

    def test_generator_creates_all_six_files(self):
        """Test that generator creates all six required files."""
        output_dir = os.path.join(self.temp_dir, "test_module")
        result = self.run_generator("test_module", "name:str,active:bool", output_dir)

        assert result.returncode == 0, f"Generator failed: {result.stderr}"

        # Check module directory was created
        assert os.path.isdir(output_dir), f"Module directory not created: {output_dir}"

        # Check all required files were created (follows project conventions)
        expected_files = [
            "__init__.py",
            "models.py",
            "schemas.py",
            "crud.py",
            "service.py",
            "router.py",
            "AGENTS.md",
        ]

        for filename in expected_files:
            filepath = os.path.join(output_dir, filename)
            assert os.path.isfile(filepath), f"File not created: {filename}"

    def test_generator_with_various_field_types(self):
        """Test generator handles different field types correctly."""
        output_dir = os.path.join(self.temp_dir, "product")
        fields = "name:str,price:Decimal,quantity:int,active:bool,created_at:datetime,metadata:json"
        result = self.run_generator("product", fields, output_dir)

        assert result.returncode == 0, f"Generator failed: {result.stderr}"

        # Check models.py contains correct field types
        models_path = os.path.join(output_dir, "models.py")
        with open(models_path) as f:
            content = f.read()

        assert "name: Mapped[str]" in content
        # Note: Decimal maps to float in generator
        assert "price: Mapped[float]" in content
        assert "quantity: Mapped[int]" in content
        assert "active: Mapped[bool]" in content
        assert "created_at: Mapped[datetime]" in content

    def test_generator_without_tenant_awareness(self):
        """Test generator creates non-tenant-aware module."""
        output_dir = os.path.join(self.temp_dir, "system_config")
        cmd = [
            "uv",
            "run",
            "python",
            "-m",
            "src.scripts.create_module",
            "system_config",
            "--fields",
            "key:str,value:str",
            "--no-tenant",
            "--output-dir",
            output_dir,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        assert result.returncode == 0, f"Generator failed: {result.stderr}"

        # Check models.py doesn't have tenant_id
        models_path = os.path.join(output_dir, "models.py")
        with open(models_path) as f:
            content = f.read()

        assert "tenant_id" not in content

    def test_generator_dry_run(self):
        """Test generator dry run mode doesn't create files."""
        output_dir = os.path.join(self.temp_dir, "dry_run_test")
        cmd = [
            "uv",
            "run",
            "python",
            "-m",
            "src.scripts.create_module",
            "dry_run_test",
            "--fields",
            "name:str",
            "--dry-run",
            "--output-dir",
            output_dir,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        assert result.returncode == 0, f"Generator failed: {result.stderr}"
        # Dry run should not create files
        assert not os.path.exists(output_dir), "Dry run should not create files"

    def test_generator_validates_field_types(self):
        """Test generator validates field types."""
        result = self.run_generator("invalid", "name:invalid_type", self.temp_dir)

        # Should fail with invalid field type
        assert result.returncode != 0 or "invalid" in result.stderr.lower()

    def test_generator_handles_existing_directory(self):
        """Test generator handles existing output directory."""
        output_dir = os.path.join(self.temp_dir, "existing")
        os.makedirs(output_dir)

        result = self.run_generator("existing", "name:str", output_dir)

        # Should succeed even with existing directory
        assert result.returncode == 0, f"Generator failed: {result.stderr}"
