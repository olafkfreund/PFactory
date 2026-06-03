# End-to-End Workflow Tests - Quick Start Guide

This guide helps you understand, run, and extend the end-to-end workflow tests for PFactory.

---

## What Are Workflow Tests?

**Workflow tests** validate complete user journeys across multiple endpoints, unlike unit tests that test individual functions in isolation.

### Example: GitLab Workflow

**Unit Test:** Tests `investigate_gitlab_issue()` alone
```python
def test_investigate_issue():
    result = investigate_gitlab_issue(project_id, issue_id)
    assert result["success"] is True
```

**Workflow Test:** Tests complete developer workflow
```python
def test_gitlab_issue_to_merge_workflow():
    # 1. Investigate issue
    issue = investigate_gitlab_issue(...)

    # 2. Create MR
    mr = create_merge_request(...)

    # 3. Run code review
    review = run_mr_review(...)

    # 4. Post review comments
    post_mr_review(...)

    # 5. Approve and merge
    approve_merge_request(...)
    merge_merge_request(...)
```

---

## Quick Start

### 1. Run All Workflow Tests

```bash
cd <your-clone-dir>/apps/web-server
pytest tests/test_e2e_workflows.py -v
```

### 2. Run Specific Workflow

```bash
# Profile management workflow
pytest tests/test_e2e_workflows.py::TestProfileManagementWorkflow::test_complete_profile_lifecycle -v

# GitLab workflow
pytest tests/test_e2e_workflows.py::TestGitLabWorkflow::test_gitlab_issue_to_mr_workflow -v

# Ideation workflow
pytest tests/test_e2e_workflows.py::TestRoadmapIdeationWorkflow::test_ideation_lifecycle_workflow -v
```

### 3. Verify Coverage

```bash
cd tests
python verify_e2e_workflows.py
```

---

## Available Workflows

### 1. Profile Management

**What it tests:** Creating, configuring, switching, and managing Claude profiles

**User story:**
> "As a developer, I want to set up multiple Claude profiles (work, personal) so I can switch between them when one hits rate limits."

**Endpoints involved:**
- Create profile → Set token → Set active → Create backup → Switch on rate limit

**Run:**
```bash
pytest tests/test_e2e_workflows.py::TestProfileManagementWorkflow -v
```

---

### 2. Roadmap & Ideation

**What it tests:** AI-powered idea generation and lifecycle management

**User story:**
> "As a product manager, I want to generate ideas for my project, triage them, and track them through to implementation."

**Endpoints involved:**
- Generate ideas → Update status → Dismiss/archive → Delete → Update roadmap

**Run:**
```bash
pytest tests/test_e2e_workflows.py::TestRoadmapIdeationWorkflow -v
```

---

### 3. GitLab Integration

**What it tests:** Complete development workflow from issue to merge

**User story:**
> "As a developer, I want to investigate an issue, create an MR, get AI code review, and merge it."

**Endpoints involved:**
- Investigate issue → Update MR → Assign reviewers → Review code → Approve → Merge

**Run:**
```bash
pytest tests/test_e2e_workflows.py::TestGitLabWorkflow -v
```

---

### 4. Project Setup

**What it tests:** Onboarding new projects into PFactory

**User story:**
> "As a new user, I want to scan my filesystem, add my project, and configure settings."

**Endpoints involved:**
- Scan projects → Add project → Configure settings → Set up environment

**Run:**
```bash
pytest tests/test_e2e_workflows.py::TestProjectSetupWorkflow -v
```

---

### 5. Settings Configuration

**What it tests:** Initial setup and configuration

**User story:**
> "As a new user, I want to set up my API keys, profiles, and preferences."

**Endpoints involved:**
- Update API key → Create profile → Configure auto-switch → Set environment

**Run:**
```bash
pytest tests/test_e2e_workflows.py::TestSettingsConfigurationWorkflow -v
```

---

### 6. Error Handling & Recovery

**What it tests:** Handling errors and recovering gracefully

**User story:**
> "As a user, when I hit rate limits or errors, I want the system to automatically switch profiles and retry."

**Endpoints involved:**
- Operation fails → Detect rate limit → Switch profile → Retry → Success

**Run:**
```bash
pytest tests/test_e2e_workflows.py::TestErrorHandlingWorkflows -v
```

---

### 7. Git Operations

**What it tests:** Git workflow management

**User story:**
> "As a developer, I want to create worktrees for parallel work, squash commits, and create releases."

**Endpoints involved:**
- Create worktree → Make commits → Squash → Create release

**Run:**
```bash
pytest tests/test_e2e_workflows.py::TestGitOperationsWorkflow -v
```

---

## Understanding Test Structure

### Anatomy of a Workflow Test

```python
class TestMyWorkflow:
    """Test [what workflow does]."""

    def test_complete_workflow(self, fixtures):
        """
        Test workflow:
        1. [First step description]
        2. [Second step description]
        3. [Third step description]
        ...

        This simulates [user scenario].
        """
        # Setup: Create test data
        test_data = {...}

        # Step 1: [Action]
        result1 = endpoint1(...)
        assert result1["success"] is True
        # Verify state changed correctly
        assert verify_step1_state()

        # Step 2: [Action]
        result2 = endpoint2(...)
        assert result2["success"] is True
        # Verify state changed correctly
        assert verify_step2_state()

        # ... more steps

        # Final validation
        assert final_state_correct()
```

### Key Components

1. **Docstring with Steps**
   - Documents what the workflow does
   - Lists each step clearly
   - Explains the user scenario

2. **Setup Phase**
   - Creates necessary test data
   - Sets up mock files
   - Configures fixtures

3. **Sequential Steps**
   - Each step represents a user action
   - Validates success after each step
   - Verifies state transitions

4. **State Verification**
   - Checks intermediate states
   - Validates data persistence
   - Ensures atomicity

---

## Adding a New Workflow Test

### Step 1: Identify the Workflow

Ask yourself:
- What is the user trying to accomplish?
- What endpoints are involved?
- What is the expected sequence?
- What should the final state be?

### Step 2: Create Test Class

```python
class TestMyNewWorkflow:
    """Test [workflow description]."""
```

### Step 3: Write Test Method

```python
def test_my_workflow(self, temp_dir, mock_settings_dir):
    """
    Test [workflow name]:
    1. [First step]
    2. [Second step]
    3. [Third step]
    ...

    This simulates [user scenario].
    """
```

### Step 4: Implement Steps

```python
# Setup
setup_test_data(temp_dir)

# Step 1
result1 = first_endpoint(...)
assert result1["success"] is True
verify_step1_state()

# Step 2
result2 = second_endpoint(...)
assert result2["success"] is True
verify_step2_state()

# ... more steps
```

### Step 5: Add Mocks (if needed)

```python
@patch("module.function")
def test_my_workflow(self, mock_function, fixtures):
    mock_function.return_value = expected_result
    # ... test implementation
```

### Step 6: Verify and Document

```bash
# Run your test
pytest tests/test_e2e_workflows.py::TestMyNewWorkflow::test_my_workflow -v

# Verify coverage
python tests/verify_e2e_workflows.py

# Update documentation in E2E_WORKFLOWS_TEST_REPORT.md
```

---

## Example: Adding a GitHub PR Workflow

Let's add a complete GitHub pull request workflow test.

### 1. Identify Workflow

**User Story:**
> "As a developer, I want to investigate a GitHub issue, create a PR, get AI review, and merge it."

**Endpoints:**
- `investigate_github_issue`
- `create_pull_request` (manual, not automated)
- `run_pr_review` (when implemented)
- `post_pr_review` (when implemented)
- `merge_pull_request` (manual confirmation)

### 2. Create Test

```python
class TestGitHubWorkflow:
    """Test complete GitHub workflow."""

    @patch("apps.web-server.server.routes.github.run_gh_command")
    @patch("apps.web-server.server.routes.github.create_simple_client")
    def test_github_pr_workflow(
        self,
        mock_ai_client,
        mock_gh,
        temp_dir,
        mock_project_dir,
        mock_projects_json
    ):
        """
        Test complete GitHub PR workflow:
        1. Investigate issue (fetch + AI analysis)
        2. Create PR (manual external step)
        3. Update PR description
        4. Assign reviewers
        5. Run AI code review
        6. Post review comments
        7. Merge PR

        This simulates a developer workflow on GitHub.
        """
        # Mock GitHub CLI responses
        issue_data = {
            "number": 456,
            "title": "Fix login bug",
            "body": "Users can't log in",
            "state": "open",
            "labels": [{"name": "bug"}],
            "user": {"login": "developer1"},
            "createdAt": "2024-01-01T10:00:00Z"
        }

        mock_gh.side_effect = [
            json.dumps(issue_data),  # get issue
            json.dumps({"comments": []}),  # get comments
            # ... more responses
        ]

        # Mock AI response
        mock_ai_response = MagicMock()
        mock_ai_response.content = json.dumps({
            "summary": "Login validation issue",
            "issue_type": "bug",
            "complexity": "simple"
        })
        mock_ai_client.return_value.messages.create.return_value = mock_ai_response

        # Patch load_projects
        with patch("apps.web-server.server.routes.github.load_projects") as mock_load:
            mock_load.return_value = json.loads(mock_projects_json.read_text())

            from apps.web_server.server.routes.github import investigate_github_issue

            # Step 1: Investigate issue
            project_id = "test-project-1"
            issue_number = 456

            mock_request = MagicMock()
            mock_request.selectedCommentIds = None

            result = investigate_github_issue(project_id, issue_number, mock_request)

            # Verify investigation worked
            assert result["issue"] is not None
            assert result["issue"]["number"] == 456
            assert "analysis" in result
            assert result["analysis"]["issue_type"] == "bug"

            # Step 2-7: Additional workflow steps would go here
            # (Some may be manual or not yet implemented)
```

### 3. Add to Verification

The verification script will automatically detect your new test class and include it in the coverage report.

### 4. Document

Add your workflow to `E2E_WORKFLOWS_TEST_REPORT.md`:

```markdown
### 8. GitHub PR Workflow

**Class:** `TestGitHubWorkflow`

**Scenarios:**
- Complete PR lifecycle on GitHub
- AI-powered code review
- Team collaboration

**Endpoints Used:**
- `investigate_github_issue`
- [Additional endpoints]

**Test Flow:**
```
1. Investigate issue
2. Create PR
3. Review code
4. Merge PR
```

**Validation:**
- ✅ GitHub CLI integration
- ✅ AI analysis
- ✅ PR workflow
```

---

## Troubleshooting

### Test Fails: "Module not found"

**Problem:** Import paths incorrect

**Solution:**
```bash
# Ensure you're in the correct directory
cd <your-clone-dir>/apps/web-server

# Install in development mode
pip install -e .

# Run tests
pytest tests/test_e2e_workflows.py
```

### Test Fails: "Fixture not found"

**Problem:** Missing fixture import

**Solution:**
```python
# Add to test file
from tests.conftest import fixture_name

# Or define fixture in test file
@pytest.fixture
def my_fixture():
    return value
```

### Test Hangs or Times Out

**Problem:** Actual network calls or CLI commands running

**Solution:**
```python
# Ensure all external calls are mocked
@patch("module.external_function")
def test_workflow(self, mock_external, ...):
    mock_external.return_value = expected_value
```

### Mock Not Working

**Problem:** Patch path incorrect

**Solution:**
```python
# Patch where it's USED, not where it's DEFINED
# ❌ Wrong
@patch("original.module.function")

# ✅ Correct
@patch("app.routes.module.function")  # Where it's imported
```

---

## Best Practices

### ✅ DO

1. **Test realistic user journeys**
   - Follow actual user workflows
   - Use realistic test data
   - Validate intermediate states

2. **Document clearly**
   - Write detailed docstrings
   - List all steps
   - Explain the user scenario

3. **Verify state at each step**
   - Don't just check final state
   - Validate transitions
   - Ensure atomicity

4. **Use descriptive names**
   - `test_complete_profile_lifecycle()` ✅
   - `test_workflow()` ❌

5. **Mock external dependencies**
   - CLI commands
   - Network calls
   - AI services

### ❌ DON'T

1. **Don't test unrelated combinations**
   - Workflows should make sense
   - Follow real user patterns

2. **Don't skip intermediate validation**
   - Verify each step
   - Don't trust the final state alone

3. **Don't use production credentials**
   - Always use mocks
   - Never commit real API keys

4. **Don't write mega-tests**
   - Keep workflows focused
   - Split if > 10 steps

5. **Don't forget error cases**
   - Test happy path AND errors
   - Validate recovery mechanisms

---

## Coverage Goals

### Current Coverage

- ✅ Profile Management: 100%
- ✅ Roadmap/Ideation: 100%
- ✅ GitLab Integration: 100%
- ⚠️ GitHub Integration: 50% (needs PR workflow)
- ✅ Git Operations: 100%
- ✅ Project Setup: 100%
- ✅ Settings Config: 100%
- ✅ Error Handling: 100%

### Target Coverage

**Goal:** 100% of major user workflows tested

**Priority Gaps:**
1. GitHub PR workflow (medium priority)
2. Concurrent access scenarios (low priority)
3. Performance workflows (low priority)

---

## CI/CD Integration

### Running in GitHub Actions

```yaml
name: E2E Workflow Tests
on: [push, pull_request]

jobs:
  e2e-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: |
          cd apps/web-server
          pip install -r requirements.txt
          pip install pytest pytest-cov pytest-xdist

      - name: Run E2E workflow tests
        run: |
          cd apps/web-server
          pytest tests/test_e2e_workflows.py -v --cov --cov-report=xml

      - name: Verify workflows
        run: |
          cd apps/web-server/tests
          python verify_e2e_workflows.py

      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          file: ./apps/web-server/coverage.xml
```

---

## Resources

### Related Files

- **Test Suite:** `tests/test_e2e_workflows.py`
- **Verification:** `tests/verify_e2e_workflows.py`
- **Report:** `tests/E2E_WORKFLOWS_TEST_REPORT.md`
- **This Guide:** `tests/README_E2E_WORKFLOWS.md`

### Other Test Suites

- Unit Tests: `tests/test_file_based_endpoints.py`
- CLI Tests: `tests/test_cli_integration_endpoints.py`
- AI Service Tests: (to be implemented)

### Documentation

- Implementation Plan: `.pfactory/specs/012-*/test_plan.json`
- Build Progress: `.pfactory/specs/012-*/build-progress.txt`
- Endpoint Tests: `tests/*_TEST_REPORT.md`

---

## Questions?

For questions or issues:

1. **Check the test report:** `E2E_WORKFLOWS_TEST_REPORT.md`
2. **Run verification:** `python verify_e2e_workflows.py`
3. **Review examples:** See existing workflow tests
4. **Check build progress:** See implementation notes

---

**Last Updated:** 2026-01-07
**Subtask:** 15.5 - End-to-end workflow testing
**Status:** ✅ COMPLETE
