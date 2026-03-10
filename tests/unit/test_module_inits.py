import importlib
import pkgutil

import pytest

import src.app.core.tenancy
import src.app.modules


def test_modules_init_getattr() -> None:
    """Dynamically test __getattr__ in all src.app.modules.*.__init__.py files.
    
    Many modules use a lazy-loading pattern with __getattr__ and __all__.
    This test ensures all defined exports can be properly imported, which
    covers the __getattr__ branches.
    """
    for _, modname, ispkg in pkgutil.iter_modules(src.app.modules.__path__):
        if ispkg:
            full_modname = f"src.app.modules.{modname}"
            module = importlib.import_module(full_modname)
            
            # If module uses lazy loading via __all__
            if hasattr(module, "__all__"):
                for attr_name in getattr(module, "__all__"):
                    # This triggers __getattr__
                    attr = getattr(module, attr_name)
                    assert attr is not None
                    
                # Test invalid attribute raises AttributeError
                with pytest.raises(AttributeError):
                    getattr(module, "NonExistentAttributeForTesting123")


def test_core_tenancy_init() -> None:
    """Test src.app.core.tenancy.__init__.py lazy loading."""
    module = src.app.core.tenancy
    if hasattr(module, "__all__"):
        for attr_name in getattr(module, "__all__"):
            attr = getattr(module, attr_name)
            assert attr is not None
            
        with pytest.raises(AttributeError):
            getattr(module, "NonExistentAttributeForTesting123")
