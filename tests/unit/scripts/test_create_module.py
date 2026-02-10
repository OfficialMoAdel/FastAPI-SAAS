"""Tests for the module generator CLI script.

Tests validate the core functionality of the module generator:
- Field parsing
- Template rendering
- File generation
- Validation logic
"""

import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

# Change import to use absolute path from root
from src.scripts.create_module import (
    FIELD_TYPE_MAP,
    FieldDefinition,
    ModuleContext,
    create_module_context,
    generate_module,
    get_template_env,
    main,
    parse_field,
    render_template,
    to_pascal_case,
    to_snake_case,
)


class TestFieldParsing:
    """Test field parsing functionality."""

    def test_parse_simple_field(self):
        """Parse a simple field like 'name:str'."""
        field = parse_field("name:str")
        assert field.name == "name"
        assert field.type == "str"
        assert field.required is True
        assert field.optional is False

    def test_parse_optional_field(self):
        """Parse an optional field like 'description:text?'."""
        field = parse_field("description:text?")
        assert field.name == "description"
        assert field.type == "text"
        assert field.required is False
        assert field.optional is True

    def test_parse_decimal_field(self):
        """Parse a Decimal field."""
        field = parse_field("price:Decimal")
        assert field.name == "price"
        assert field.type == "Decimal"

    def test_parse_datetime_field(self):
        """Parse a datetime field."""
        field = parse_field("created_at:datetime")
        assert field.name == "created_at"
        assert field.type == "datetime"

    def test_parse_json_field(self):
        """Parse a JSON field."""
        field = parse_field("metadata:json")
        assert field.name == "metadata"
        assert field.type == "json"

    def test_parse_uuid_field(self):
        """Parse a UUID field."""
        field = parse_field("id:uuid")
        assert field.name == "id"
        assert field.type == "uuid"

    def test_parse_int_field(self):
        """Parse an integer field."""
        field = parse_field("quantity:int")
        assert field.name == "quantity"
        assert field.type == "int"

    def test_parse_bool_field(self):
        """Parse a boolean field."""
        field = parse_field("is_active:bool")
        assert field.name == "is_active"
        assert field.type == "bool"

    def test_parse_float_field(self):
        """Parse a float field."""
        field = parse_field("rating:float")
        assert field.name == "rating"
        assert field.type == "float"

    def test_parse_invalid_format(self):
        """Handle invalid field format gracefully."""
        with pytest.raises(Exception):
            parse_field("invalid_field_no_type")


class TestFieldDefinition:
    """Test FieldDefinition class methods."""

    def test_sqlalchemy_column_str_required(self):
        """Get SQLAlchemy column for required string."""
        field = FieldDefinition(name="title", type="str", required=True)
        col = field.get_sqlalchemy_column()
        assert "Mapped[str]" in col
        assert "nullable=False" in col

    def test_sqlalchemy_column_str_optional(self):
        """Get SQLAlchemy column for optional string."""
        field = FieldDefinition(name="description", type="str", required=False)
        col = field.get_sqlalchemy_column()
        assert "Mapped[str | None]" in col
        assert "default=None" in col

    def test_sqlalchemy_column_decimal(self):
        """Get SQLAlchemy column for Decimal."""
        field = FieldDefinition(name="price", type="Decimal", required=True)
        col = field.get_sqlalchemy_column()
        assert "Mapped[Decimal]" in col
        assert "Numeric" in col

    def test_sqlalchemy_column_datetime(self):
        """Get SQLAlchemy column for datetime."""
        field = FieldDefinition(name="created_at", type="datetime", required=True)
        col = field.get_sqlalchemy_column()
        assert "Mapped[datetime]" in col
        assert "DateTime" in col

    def test_pydantic_field_str_required(self):
        """Get Pydantic field for required string."""
        field = FieldDefinition(name="title", type="str", required=True)
        pydantic = field.get_pydantic_field()
        assert "title: Annotated[str" in pydantic
        assert "Field(min_length=1" in pydantic

    def test_pydantic_field_str_optional(self):
        """Get Pydantic field for optional string."""
        field = FieldDefinition(name="description", type="str", required=False)
        pydantic = field.get_pydantic_field()
        assert "description: str | None = Field(default=None)" in pydantic

    def test_pydantic_field_for_update(self):
        """Get Pydantic field for update schema."""
        field = FieldDefinition(name="title", type="str", required=True)
        pydantic = field.get_pydantic_field(for_update=True)
        assert "title: str | None = Field(default=None)" in pydantic


class TestModuleContext:
    """Test ModuleContext helper properties."""

    def test_has_decimal_true(self):
        """Detect when module has Decimal fields."""
        fields = [
            FieldDefinition(name="price", type="Decimal", required=True),
            FieldDefinition(name="name", type="str", required=True),
        ]
        ctx = ModuleContext(
            module_name="products",
            model_name="Product",
            table_name="products",
            fields=fields,
        )
        assert ctx.has_decimal is True

    def test_has_decimal_false(self):
        """Detect when module has no Decimal fields."""
        fields = [
            FieldDefinition(name="name", type="str", required=True),
            FieldDefinition(name="quantity", type="int", required=True),
        ]
        ctx = ModuleContext(
            module_name="products",
            model_name="Product",
            table_name="products",
            fields=fields,
        )
        assert ctx.has_decimal is False

    def test_has_datetime_true(self):
        """Detect when module has datetime fields."""
        fields = [
            FieldDefinition(name="created_at", type="datetime", required=True),
        ]
        ctx = ModuleContext(
            module_name="events",
            model_name="Event",
            table_name="events",
            fields=fields,
        )
        assert ctx.has_datetime is True

    def test_has_json_true(self):
        """Detect when module has JSON fields."""
        fields = [
            FieldDefinition(name="metadata", type="json", required=False),
        ]
        ctx = ModuleContext(
            module_name="items",
            model_name="Item",
            table_name="items",
            fields=fields,
        )
        assert ctx.has_json is True

    def test_has_uuid_true(self):
        """Detect when module has UUID fields."""
        fields = [
            FieldDefinition(name="uuid", type="uuid", required=True),
        ]
        ctx = ModuleContext(
            module_name="entities",
            model_name="Entity",
            table_name="entities",
            fields=fields,
        )
        assert ctx.has_uuid is True

    def test_has_text_true(self):
        """Detect when module has text fields."""
        fields = [
            FieldDefinition(name="description", type="text", required=False),
        ]
        ctx = ModuleContext(
            module_name="posts",
            model_name="Post",
            table_name="posts",
            fields=fields,
        )
        assert ctx.has_text is True


class TestStringHelpers:
    """Test string case conversion functions."""

    def test_to_snake_case_simple(self):
        """Convert simple PascalCase to snake_case."""
        assert to_snake_case("Product") == "product"

    def test_to_snake_case_compound(self):
        """Convert compound PascalCase to snake_case."""
        assert to_snake_case("ProductCategory") == "product_category"

    def test_to_snake_case_already_snake(self):
        """Handle already snake_case string."""
        assert to_snake_case("product_category") == "product_category"

    def test_to_pascal_case_simple(self):
        """Convert simple snake_case to PascalCase."""
        assert to_pascal_case("product") == "Product"

    def test_to_pascal_case_compound(self):
        """Convert compound snake_case to PascalCase."""
        assert to_pascal_case("product_category") == "ProductCategory"

    def test_to_pascal_case_already_pascal(self):
        """Handle already PascalCase string."""
        assert to_pascal_case("Product") == "Product"


class TestModuleContextCreation:
    """Test create_module_context function."""

    def test_create_context_simple(self):
        """Create context with simple fields."""
        ctx = create_module_context("products", "name:str,price:Decimal")
        assert ctx.module_name == "products"
        assert ctx.model_name == "Product"
        assert ctx.table_name == "products"
        assert len(ctx.fields) == 2
        assert ctx.tenant_aware is True

    def test_create_context_no_tenant(self):
        """Create context without tenant awareness."""
        ctx = create_module_context("tags", "name:str,color:str", tenant_aware=False)
        assert ctx.tenant_aware is False

    def test_create_context_empty_fields(self):
        """Create context with empty fields."""
        ctx = create_module_context("simple", "")
        assert len(ctx.fields) == 0


class TestFieldTypeMap:
    """Test FIELD_TYPE_MAP configuration."""

    def test_all_types_have_required_keys(self):
        """All field types have required configuration keys."""
        required_keys = ["sqlalchemy_column", "sqlalchemy_type", "pydantic_type", "pydantic_annotated"]
        for field_type, config in FIELD_TYPE_MAP.items():
            for key in required_keys:
                assert key in config, f"Field type '{field_type}' missing key '{key}'"

    def test_str_type_config(self):
        """String type has correct configuration."""
        config = FIELD_TYPE_MAP["str"]
        assert config["sqlalchemy_column"] == "String(255)"
        assert config["sqlalchemy_type"] == "String"
        assert config["pydantic_type"] == "str"

    def test_decimal_type_config(self):
        """Decimal type has correct configuration."""
        config = FIELD_TYPE_MAP["Decimal"]
        assert "Numeric" in config["sqlalchemy_column"]
        assert config["pydantic_type"] == "Decimal"

    def test_int_type_config(self):
        """Integer type has correct configuration."""
        config = FIELD_TYPE_MAP["int"]
        assert config["sqlalchemy_column"] == "Integer"
        assert config["sqlalchemy_type"] == "Integer"
        assert config["pydantic_type"] == "int"

    def test_bool_type_config(self):
        """Boolean type has correct configuration."""
        config = FIELD_TYPE_MAP["bool"]
        assert config["sqlalchemy_column"] == "Boolean"
        assert config["pydantic_type"] == "bool"


class TestTemplateRendering:
    """Test template rendering functions."""

    def test_get_template_env(self):
        """Get Jinja2 template environment."""
        env = get_template_env()
        assert env is not None

    @patch("scripts.create_module.get_template_env")
    def test_render_template(self, mock_get_env):
        """Render a template with context."""
        mock_template = MagicMock()
        mock_template.render.return_value = "# Generated Content"
        mock_env = MagicMock()
        mock_env.get_template.return_value = mock_template
        mock_get_env.return_value = mock_env

        ctx = create_module_context("products", "name:str")
        result = render_template("models.py.j2", ctx)

        mock_env.get_template.assert_called_once_with("models.py.j2")
        mock_template.render.assert_called_once()
        assert result == "# Generated Content"


class TestModuleGeneration:
    """Test module generation functionality."""

    @patch("pathlib.Path.mkdir")
    @patch("builtins.open", new_callable=mock_open)
    @patch("scripts.create_module.render_template")
    @patch("scripts.create_module.get_template_env")
    def test_generate_module_creates_files(self, mock_get_env, mock_render, mock_file, mock_mkdir):
        """Generate module creates expected files."""
        mock_render.return_value = "# Test content"
        mock_env = MagicMock()
        mock_get_env.return_value = mock_env

        ctx = create_module_context("products", "name:str,price:Decimal")
        output_dir = Path("/tmp/test_module")

        # Create files should not raise
        generate_module(mock_env, ctx, output_dir, dry_run=False, force=False)

        # Should attempt to create directory
        mock_mkdir.assert_called_with(parents=True, exist_ok=True)

    def test_generate_module_dry_run(self):
        """Dry run should not write files."""
        ctx = create_module_context("products", "name:str")
        output_dir = Path("/tmp/test_module")

        # Should not raise in dry-run mode
        with patch("builtins.open") as mock_file:
            generate_module(None, ctx, output_dir, dry_run=True, force=False)
            mock_file.assert_not_called()


class TestMainFunction:
    """Test main CLI function."""

    @patch("scripts.create_module.generate_module")
    @patch("scripts.create_module.create_module_context")
    @patch("scripts.create_module.get_template_env")
    def test_main_success(self, mock_get_env, mock_create_ctx, mock_gen_mod):
        """Test successful main execution."""
        mock_ctx = MagicMock()
        mock_ctx.module_name = "products"
        mock_create_ctx.return_value = mock_ctx
        mock_env = MagicMock()
        mock_get_env.return_value = mock_env

        # Mock command line arguments
        test_args = [
            "create_module.py",
            "products",
            "--fields",
            "name:str,price:Decimal",
        ]

        with patch.object(sys, "argv", test_args):
            result = main()
            assert result == 0

    @patch("scripts.create_module.create_module_context")
    def test_main_invalid_module_name(self, mock_create_ctx):
        """Test main with invalid module name."""
        mock_create_ctx.side_effect = ValueError("Invalid module name")

        test_args = ["create_module.py", "invalid-name"]

        with patch.object(sys, "argv", test_args):
            result = main()
            assert result == 1

    def test_main_no_args(self):
        """Test main with no arguments shows help."""
        test_args = ["create_module.py"]

        with patch.object(sys, "argv", test_args):
            with pytest.raises(SystemExit) as exc_info:
                main()
            # argparse exits with code 2 for missing arguments
            assert exc_info.value.code == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])