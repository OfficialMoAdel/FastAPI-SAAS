# Modular Architecture

The project employs a modular architecture designed for scalability and maintainability. This approach allows developers to build complex features as independent units while sharing common infrastructure.

## The `_base` Pattern

To ensure consistency across the application, we use a set of base classes located in `src/app/modules/_base/`. These classes provide standardized patterns for common operations.

### Base Service

The `BaseService` class is a generic orchestrator that wraps [FastCRUD](https://github.com/igorbenav/fastcrud) operations. It provides standard methods for:
- `get(id)`: Retrieve a single record.
- `get_multi(page, per_page)`: Paginated retrieval of multiple records.
- `create(**kwargs)`: Create a new record.
- `update(id, **kwargs)`: Update an existing record.
- `delete(id)`: Remove a record.

By inheriting from `BaseService`, your feature services immediately support standard CRUD without writing boilerplate code.

### Base Schemas

Standardized Pydantic schemas ensure that all API endpoints follow consistent request and response formats.

- **`BaseCreate`**: Enforces strict validation for creation requests.
- **`BaseRead`**: Includes standard fields like `id`, `created_at`, and `updated_at`.
- **`BaseReadUUID`**: Similar to `BaseRead` but uses UUID as the primary key.
- **`BaseUpdate`**: Allows partial updates with optional fields.

## Creating a New Module

When adding a new feature, follow these steps:

1. **Define the Model**: Create a new class in `models.py` inheriting from `Base`.
2. **Create Schemas**: Define Create, Read, and Update schemas in `schemas.py` using the `_base` patterns.
3. **Implement Service**: Create a service in `service.py` inheriting from `BaseService[Model]`.
4. **Define Router**: Set up endpoints in `router.py`, injecting your service via FastAPI's `Depends`.
5. **Register Module**: Include the new router in `src/app/api/v1/api.py`.

## Cross-Module Communication

Modules should avoid direct imports of each other's services where possible. Instead, use the **Event Bus** in `src/app/core/events/` to emit and listen for actions across the system. This maintains loose coupling and improves testability.
