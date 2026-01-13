from unittest.mock import Mock, patch

from src.app.core.events import EventBus, event_bus


class TestEventRegistration:
    def test_initial_state(self):
        """Test event bus starts in not ready state."""
        bus = EventBus()
        assert bus.is_ready is False
        assert len(bus._handlers) == 0

    @patch("importlib.import_module")
    def test_register_all_handlers_success(self, mock_import):
        """Test successful registration of handlers."""
        bus = EventBus()

        # Mock _scan_handler_modules to return fixed list
        bus._scan_handler_modules = Mock(
            return_value=["src.app.modules.module1.service", "src.app.modules.module2.service"]
        )

        bus.register_all_handlers()

        assert bus.is_ready is True

        # Check expected calls exist
        mock_import.assert_any_call("src.app.modules.module1.service")
        mock_import.assert_any_call("src.app.modules.module2.service")

    @patch("importlib.import_module")
    def test_register_all_handlers_import_error(self, mock_import):
        """Test registration handles import errors gracefully."""
        bus = EventBus()

        # Configure mock to raise ImportError only for specific module
        def import_side_effect(name, *args, **kwargs):
            if name == "src.app.modules.module1.service":
                raise ImportError("Test error")
            return Mock()

        mock_import.side_effect = import_side_effect

        # Mock _scan_handler_modules
        bus._scan_handler_modules = Mock(
            return_value=["src.app.modules.module1.service", "src.app.modules.module2.service"]
        )

        # Should not raise exception
        bus.register_all_handlers()

        assert bus.is_ready is True

        # Check that both imports were attempted
        calls = [c.args[0] for c in mock_import.call_args_list]
        assert "src.app.modules.module1.service" in calls
        assert "src.app.modules.module2.service" in calls

    def test_singleton_initialization(self):
        """Test global event bus initialization."""
        assert isinstance(event_bus, EventBus)
