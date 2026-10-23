# Backend tests

This folder contains the backend test suite for Voyage AI.

The tests check chat orchestration, state handling, provider validation, package building, cart handling, checkout workflow, destination discovery and robustness behaviour.

These tests support the dissertation evidence that the backend workflow was tested through unit and integration-style tests.

To run the tests from the backend folder:

```bash
python -m pytest
```

Some tests may require local environment variables or test configuration. Real provider keys should be stored in the backend .env file and should not be committed to the repository.

The `_archive_not_submitted` folder contains old or temporary test files that are not intended for final submission.
