# Fix datetime Bugs (UTC undefined & Static Evaluation)

## Checklist
- [ ] Fix `src/app/modules/ai/token_usage.py`: Use `datetime.now(timezone.utc)`
- [ ] Fix `src/app/modules/dashboard/schemas.py`: Use `lambda: datetime.now(timezone.utc)`
- [ ] Fix `src/app/modules/files/models.py`: Use `onupdate=lambda: datetime.now(timezone.utc)`
- [ ] Fix `src/app/modules/sso/models.py`: Use `onupdate=lambda: datetime.now(timezone.utc)`
- [ ] Fix `src/app/modules/sso_settings/models.py`: Use `onupdate=lambda: datetime.now(timezone.utc)`
- [ ] Fix `src/app/modules/storage_settings/models.py`: Use `onupdate=lambda: datetime.now(timezone.utc)`
- [ ] Fix `src/app/modules/user_preferences/models.py`: Use `onupdate=lambda: datetime.now(timezone.utc)`
- [ ] Run tests to verify
