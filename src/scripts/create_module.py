#!/usr/bin/env python3
"""Module Generator CLI for FastAPI SaaS boilerplate.

Generates a complete module with all 6 files pre-configured following
the established patterns in the codebase.

Usage:
    uv run python -m src.scripts.create_module products \
        --fields 'name:str price:Decimal quantity:int'

    uv run python -m src.scripts.create_module tags \
        --fields 'name:str color:str' --no-tenant

    uv run python -m src.scripts.create_module invoices \
        --fields 'number:str amount:Decimal status:str due_date:datetime' \
        --dry-run

"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

# ============================================================================
# Field Type Mapping
# ============================================================================

FIELD_TYPE_MAP: dict[str, dict[str, Any]] = {
    "str": {
        "pydantic_type": "str",
        "pydantic_annotated": "Annotated[str, StringConstraints(max_length=255)]",
        "default": '""',
        "sqlalchemy_column": "String(255)",
        "sqlalchemy_type": "String",
    },
    "int": {
        "pydantic_type": "int",
        "pydantic_annotated": "int",
        "default": "0",
        "sqlalchemy_column": "Integer",
        "sqlalchemy_type": "Integer",
    },
    "bool": {
        "pydantic_type": "bool",
        "pydantic_annotated": "bool",
        "default": "False",
        "sqlalchemy_column": "Boolean",
        "sqlalchemy_type": "Boolean",
    },
    "float": {
        "pydantic_type": "float",
        "pydantic_annotated": "float",
        "default": "0.0",
        "sqlalchemy_column": "Float",
        "sqlalchemy_type": "Float",
    },
    "Decimal": {
        "pydantic_type": "Decimal",
        "pydantic_annotated": "Decimal",
        "default": 'Decimal("0.00")',
        "sqlalchemy_column": "Numeric(10, 2)",
        "sqlalchemy_type": "Numeric",
        "import_decimal": True,
    },
    "datetime": {
        "pydantic_type": "datetime",
        "pydantic_annotated": "datetime",
        "default": "func.now()",
        "sqlalchemy_column": "DateTime(timezone=True)",
        "sqlalchemy_type": "DateTime",
        "import_datetime": True,
    },
    "text": {
        "pydantic_type": "str",
        "pydantic_annotated": "str",
        "default": '""',
        "sqlalchemy_column": "Text",
        "sqlalchemy_type": "Text",
    },
    "json": {
        "pydantic_type": "dict",
        "pydantic_annotated": "dict",
        "default": "{}",
        "sqlalchemy_column": "JSON",
        "sqlalchemy_type": "JSON",
    },
    "uuid": {
        "pydantic_type": "UUID",
        "pydantic_annotated": "UUID",
        "default": "uuid7",
        "sqlalchemy_column": "UUID(as_uuid=True)",
        "sqlalchemy_type": "UUID",
        "import_uuid": True,
    },
}


@dataclass
class FieldDefinition:
    """Represents a field definition for the module."""

    name: str
    type: str
    required: bool = True
    optional: bool = False

    @property
    def is_optional(self) -> bool:
        """Check if the field is optional in the database."""
        return not self.required or self.optional

    def get_sqlalchemy_column(self) -> str:
        """Get the SQLAlchemy column definition."""
        type_info = FIELD_TYPE_MAP.get(self.type, FIELD_TYPE_MAP["str"])
        column_type = type_info["sqlalchemy_column"]

        if self.is_optional:
            return f"Mapped[{type_info['pydantic_type']} | None] = mapped_column({column_type}, default=None)"
        return f"Mapped[{type_info['pydantic_type']}] = mapped_column({column_type}, nullable=False)"

    def get_pydantic_field(self, for_update: bool = False) -> str:
        """Get the Pydantic field definition."""
        type_info = FIELD_TYPE_MAP.get(self.type, FIELD_TYPE_MAP["str"])
        base_type = type_info["pydantic_type"]

        if for_update:
            return f"{self.name}: {base_type} | None = Field(default=None)"

        if self.is_optional:
            return f"{self.name}: {base_type} | None = Field(default=None)"

        if self.type == "str":
            return f"{self.name}: Annotated[str, Field(min_length=1, max_length=255)]"

        return f"{self.name}: {base_type}"


@dataclass
class ModuleContext:
    """Context for template rendering."""

    module_name: str  # snake_case: products
    model_name: str  # PascalCase: Product
    table_name: str  # snake_case: products
    fields: list[FieldDefinition]
    tenant_aware: bool = True

    @property
    def has_decimal(self) -> bool:
        """Check if any field uses Decimal type."""
        return any(f.type == "Decimal" for f in self.fields)

    @property
    def has_datetime(self) -> bool:
        """Check if any field uses datetime type."""
        return any(f.type == "datetime" for f in self.fields)

    @property
    def has_json(self) -> bool:
        """Check if any field uses JSON type."""
        return any(f.type == "json" for f in self.fields)

    @property
    def has_uuid(self) -> bool:
        """Check if any field uses UUID type."""
        return any(f.type == "uuid" for f in self.fields)

    @property
    def has_text(self) -> bool:
        """Check if any field uses Text type."""
        return any(f.type == "text" for f in self.fields)


def to_snake_case(name: str) -> str:
    """Convert CamelCase to snake_case."""
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def to_pascal_case(name: str) -> str:
    """Convert snake_case to PascalCase."""
    return "".join(word.capitalize() for word in name.split("_"))


# Common pluralization rules for singularization
SINGULAR_RULES = [
    (r"ies$", "y"),  # categories -> category
    (r"es$", ""),  # boxes -> box, addresses -> address
    (r"s$", ""),  # products -> product
]


def to_singular(name: str) -> str:
    """Convert a plural word to singular using common rules."""
    import re

    for pattern, replacement in SINGULAR_RULES:
        if re.search(pattern, name):
            return re.sub(pattern, replacement, name)
    return name


def parse_field(field_str: str) -> FieldDefinition:
    """Parse a field definition string.

    Args:
        field_str: Field definition in format "name:type" or "name:type?" for optional

    Returns:
        FieldDefinition instance

    Raises:
        ValueError: If the field format is invalid
    """
    # Handle optional field marker (name:type?)
    if field_str.endswith("?"):
        field_str = field_str[:-1]
        optional = True
    else:
        optional = False

    parts = field_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid field format: '{field_str}'. Expected format: 'name:type'")

    name, field_type = parts
    name = name.strip()
    field_type = field_type.strip()

    # Validate field type
    if field_type not in FIELD_TYPE_MAP:
        valid_types = ", ".join(FIELD_TYPE_MAP.keys())
        raise ValueError(f"Invalid field type: '{field_type}'. Valid types: {valid_types}")

    return FieldDefinition(
        name=name,
        type=field_type,
        required=not optional,
        optional=optional,
    )


def create_module_context(module_name: str, fields_str: str, tenant_aware: bool = True) -> ModuleContext:
    """Create a module context from CLI arguments.

    Args:
        module_name: The module name in snake_case
        fields_str: Comma-separated field definitions
        tenant_aware: Whether the module should be tenant-aware

    Returns:
        ModuleContext instance
    """
    # Parse module name
    module_name = to_snake_case(module_name)
    # Model name is singular (products -> Product)
    singular_name = to_singular(module_name)
    model_name = to_pascal_case(singular_name)
    # Table names are usually singular in this codebase
    table_name = module_name

    # Parse fields
    fields = []
    if fields_str:
        for field_def in fields_str.split(","):
            field_def = field_def.strip()
            if field_def:
                fields.append(parse_field(field_def))

    return ModuleContext(
        module_name=module_name,
        model_name=model_name,
        table_name=table_name,
        fields=fields,
        tenant_aware=tenant_aware,
    )


def get_template_env() -> Environment:
    """Get the Jinja2 template environment."""
    template_dir = Path(__file__).parent / "templates"
    return Environment(
        loader=FileSystemLoader(template_dir),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_template(template_name: str, context: ModuleContext) -> str:
    """Render a template with the given context.

    Args:
        template_name: Name of the template file
        context: Module context for rendering

    Returns:
        Rendered template as string
    """
    env = get_template_env()
    template = env.get_template(f"{template_name}.j2")
    return template.render(
        module_name=context.module_name,
        model_name=context.model_name,
        table_name=context.table_name,
        fields=context.fields,
        tenant_aware=context.tenant_aware,
        has_decimal=context.has_decimal,
        has_datetime=context.has_datetime,
        has_json=context.has_json,
        has_uuid=context.has_uuid,
        has_text=context.has_text,
    )


def generate_module(
    context: ModuleContext,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Path]:
    """Generate all module files.

    Args:
        context: Module context
        dry_run: If True, print what would be created without creating files
        force: If True, overwrite existing files

    Returns:
        Dictionary of file names to file paths
    """
    # Define the module directory
    module_dir = Path("src/app/modules") / context.module_name

    if dry_run:
        print(f"\n{'=' * 60}")
        print("DRY RUN: Would create the following structure:")
        print(f"{'=' * 60}")
        print(f"\n{module_dir}/")
        print("  ├── __init__.py")
        print("  ├── models.py")
        print("  ├── schemas.py")
        print("  ├── crud.py")
        print("  ├── service.py")
        print("  ├── router.py")
        print("  └── AGENTS.md")
        print(f"\n{'=' * 60}")
        return {}

    # Create directory if it doesn't exist
    if not module_dir.exists():
        module_dir.mkdir(parents=True)
        print(f"Created directory: {module_dir}")

    # Generate files
    files_to_generate = {
        "__init__.py": render_template("init", context),
        "models.py": render_template("models", context),
        "schemas.py": render_template("schemas", context),
        "crud.py": render_template("crud", context),
        "service.py": render_template("service", context),
        "router.py": render_template("router", context),
        "AGENTS.md": render_template("agents", context),
    }

    generated_files: dict[str, Path] = {}

    for filename, content in files_to_generate.items():
        file_path = module_dir / filename

        if file_path.exists() and not force:
            print(f"Skipping existing file: {file_path}")
            continue

        file_path.write_text(content, encoding="utf-8")
        generated_files[filename] = file_path
        print(f"Generated: {file_path}")

    return generated_files


def main() -> int:
    """Main entry point for the CLI.

    Returns:
        Exit code (0 for success, 1 for error)
    """
    parser = argparse.ArgumentParser(
        description="Generate a complete FastAPI SaaS module",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate a basic module with string fields
  uv run python -m src.scripts.create_module products --fields 'name:str,price:Decimal'

  # Generate without tenant awareness (rare)
  uv run python -m src.scripts.create_module tags --fields 'name:str,color:str' --no-tenant

  # Preview what would be generated
  uv run python -m src.scripts.create_module invoices --fields 'number:str,amount:Decimal' --dry-run

  # Overwrite existing files
  uv run python -m src.scripts.create_module products --force

Field Types:
  str      → String(255), str
  int      → Integer, int
  bool     → Boolean, bool
  float    → Float, float
  Decimal  → Numeric(10, 2), Decimal
  datetime → DateTime(timezone=True), datetime
  text     → Text, str
  json     → JSONB, dict[str, Any]
  uuid     → UUID(as_uuid=True), UUID

Optional fields: Add '?' suffix (e.g., 'description:text?')
        """,
    )

    parser.add_argument(
        "module_name",
        help="Name of the module in snake_case (e.g., 'products', 'blog_posts')",
    )

    parser.add_argument(
        "--fields",
        type=str,
        default="",
        help="Comma-separated field definitions (e.g., 'name:str,price:Decimal')",
    )

    parser.add_argument(
        "--no-tenant",
        action="store_true",
        help="Generate module without tenant awareness (rare, most modules are tenant-aware)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be generated without creating files",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files",
    )

    args = parser.parse_args()

    try:
        # Create module context
        context = create_module_context(
            module_name=args.module_name,
            fields_str=args.fields,
            tenant_aware=not args.no_tenant,
        )

        # Generate module
        generated = generate_module(
            context=context,
            dry_run=args.dry_run,
            force=args.force,
        )

        if not args.dry_run and generated:
            print(f"\n{'=' * 60}")
            print(f"Successfully generated module: {args.module_name}")
            print(f"{'=' * 60}")
            print("\nNext steps:")
            print(f"  1. Review the generated code in src/app/modules/{args.module_name}/")
            print(f"  2. Run type check: uv run mypy src/app/modules/{args.module_name}/")
            print(f"  3. Run linter: uv run ruff check src/app/modules/{args.module_name}/")
            print("  4. Add router to src/app/api/v1/api.py:")
            print(f"     from src.app.modules.{args.module_name}.router import router as {args.module_name}_router")
            print(f"     api_router.include_router({args.module_name}_router)")
            print(f"  5. Generate migrations: uv run alembic revision --autogenerate -m 'Add {args.module_name} table'")

        return 0

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
