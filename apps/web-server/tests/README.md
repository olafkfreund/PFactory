# AIFactory Web Server Tests

This directory contains test suites and verification tools for the AIFactory web server backend.

## Test Files

### 1. test_file_based_endpoints.py
**Comprehensive pytest test suite for all 26 file-based endpoint implementations.**

- **Purpose**: Unit and integration testing for file-based operations
- **Coverage**: 26 endpoints across 8 phases (Phase 2-5, 9, 11-13)
- **Testing Approach**:
  - Pytest fixtures for mock data and temporary directories
  - Test cases for each endpoint
  - Security feature verification
  - End-to-end workflow testing

**Usage**:
```bash
cd apps/web-server/tests
pytest test_file_based_endpoints.py -v
```

**Test Categories**:
- Phase 2: Critical Priority - Settings & Core Config (7 tests)
- Phase 3: Important Priority - Profile Management (4 tests)
- Phase 4: Important Priority - API Profile Management (2 tests)
- Phase 5: Important Priority - Ideation File Operations (3 tests)
- Phase 9: Context Management (1 test)
- Phase 11: Low Priority - Bulk Operations (2 tests)
- Phase 12: Low Priority - Media & Session Management (4 tests)
- Phase 13: Low Priority - Project & Environment (2 tests)
- Security Features (3 test classes)
- Integration Workflows (3 test classes)

### 2. verify_file_based_endpoints.py
**Automated verification script that validates all file-based endpoint implementations.**

- **Purpose**: Static code analysis to verify endpoints are implemented
- **Method**: Regex-based function detection and stub pattern matching
- **Output**: Detailed report with verification status for each endpoint

**Usage**:
```bash
cd apps/web-server/tests
python3 verify_file_based_endpoints.py
```

**Verification Checks**:
- ✅ Function exists in route file
- ✅ Not a stub implementation
- ✅ Has validation logic
- ✅ Has file I/O operations
- ✅ Has error handling
- ✅ Success rate calculation

### 3. FILE_BASED_ENDPOINTS_TEST_REPORT.md
**Comprehensive test report documenting verification of all 26 file-based endpoints.**

- **Purpose**: Documentation of test results and verification methods
- **Content**:
  - Executive summary with 100% success rate
  - Detailed verification for each of 26 endpoints
  - Security features summary
  - Test coverage matrix
  - Integration testing documentation

**Key Highlights**:
- All 26 endpoints verified through multiple methods
- Code inspection + implementation plan validation
- Security features verified (0o600 permissions, input validation)
- Commit references for each implementation

## File-Based Endpoints Coverage

### What are File-Based Endpoints?
File-based endpoints are API routes that primarily perform read/write operations on JSON configuration files. They don't require external CLI tools or AI service integration.

**Total Coverage**: 26 endpoints (out of 46 total endpoints in project)

### Endpoint Categories

1. **Settings Management** (12 endpoints)
   - Claude profiles (5)
   - API profiles (4)
   - Auto-switch configuration (1)
   - Source environment (2)

2. **Project Configuration** (5 endpoints)
   - Project settings (1)
   - Feature status (1)
   - Idea status (1)
   - Project scanning (1)
   - Project environment (1)

3. **Ideation Operations** (5 endpoints)
   - Dismiss/archive/delete single idea (3)
   - Bulk dismiss/delete ideas (2)

4. **Media & Session Management** (4 endpoints)
   - Save changelog image (1)
   - Clear insights sessions (2)
   - Save terminal buffer (1)

## Testing Infrastructure

### Pytest Fixtures
All tests use the following fixtures for consistency:

- **temp_dir**: Temporary directory for test files
- **mock_settings_dir**: Mock .aifactory directory structure
- **mock_claude_profiles**: Mock claude-profiles.json with sample data
- **mock_api_profiles**: Mock api-profiles.json with sample data
- **mock_projects**: Mock projects.json with test project
- **mock_ideation**: Mock ideation.json with sample ideas
- **mock_roadmap**: Mock roadmap.json with sample features

### Security Testing

All file-based endpoints are tested for:

1. **Secure File Permissions**
   - Sensitive files (profiles, .env) must have 0o600 permissions
   - Owner-only read/write access

2. **Input Validation**
   - Empty checks
   - Whitespace stripping
   - Length validation (1-100 chars for names, min lengths for tokens)
   - Format validation (URLs, API keys, tokens)
   - Duplicate prevention

3. **Atomic Operations**
   - Read → Modify → Write pattern
   - Directory creation before write
   - JSON validation before save

4. **Error Handling**
   - HTTPException for appropriate status codes
   - JSON decode error handling
   - File system error handling
   - Clear error messages

## Running Tests

### Prerequisites
```bash
# Install pytest if not already installed
pip install pytest pytest-asyncio

# Install FastAPI testing dependencies
pip install httpx
```

### Run All Tests
```bash
cd apps/web-server/tests
pytest test_file_based_endpoints.py -v
```

### Run Specific Test Class
```bash
pytest test_file_based_endpoints.py::TestPhase2CriticalPrioritySettings -v
```

### Run Verification Script
```bash
python3 verify_file_based_endpoints.py
```

### Expected Output
```
================================================================================
Verifying 26 File-Based Endpoint Implementations
================================================================================

Phase 2: Critical Priority - Settings & Core Config (7 endpoints)
✅ 2.1 - update_api_key (settings.py)
✅ 2.2 - set_active_profile (settings.py)
...

VERIFICATION SUMMARY
================================================================================
Total Endpoints: 26
✅ Verified: 26
Success Rate: 100.0%
```

## Test Development Guidelines

When adding new file-based endpoint tests:

1. **Add to appropriate test class** based on phase
2. **Create necessary fixtures** for mock data
3. **Test validation logic** - empty checks, format validation
4. **Test security features** - file permissions, input sanitization
5. **Test error cases** - invalid input, missing files, JSON errors
6. **Test success cases** - verify expected behavior
7. **Update verification script** to include new endpoint
8. **Update test report** with implementation details

---

## CLI Integration Endpoints Coverage

### 4. test_cli_integration_endpoints.py
**Comprehensive pytest test suite for all 10 CLI integration endpoint implementations.**

- **Purpose**: Unit testing for CLI command execution with mocked commands
- **Coverage**: 10 endpoints across 4 phases (Phase 7, 9, 10, 14)
- **Testing Approach**:
  - Mock CLI commands (glab, gh, git, claude)
  - Test success and error paths
  - Validate command building logic
  - Test input validation and error handling

**Usage**:
```bash
cd apps/web-server/tests
pytest test_cli_integration_endpoints.py -v
```

**Test Categories**:
- Phase 7: GitLab CLI Operations (5 tests)
- Phase 9: Context Management (1 test)
- Phase 10: Git Operations (2 tests)
- Phase 14: Git Maintenance & Reviews (2 tests)

### 5. verify_cli_integration_endpoints.py
**Automated verification script that validates all CLI integration endpoint implementations.**

- **Purpose**: Static code analysis to verify CLI endpoints are implemented
- **Method**: Regex-based detection of CLI command execution
- **Output**: Detailed report with verification status for each endpoint

**Usage**:
```bash
cd apps/web-server/tests
python3 verify_cli_integration_endpoints.py
```

**Verification Checks**:
- ✅ Function exists in route file
- ✅ Not a stub implementation
- ✅ Has CLI command execution (run_glab_command, run_git_command, etc.)
- ✅ Has error handling
- ✅ Has input validation
- ✅ Success rate calculation

### 6. CLI_INTEGRATION_ENDPOINTS_TEST_REPORT.md
**Comprehensive test report documenting verification of all 10 CLI integration endpoints.**

- **Purpose**: Documentation of CLI test results and verification methods
- **Content**:
  - Executive summary with 100% success rate
  - Detailed verification for each of 10 endpoints
  - CLI tool breakdown (glab, gh, git, claude)
  - Security features and command injection prevention
  - Test coverage matrix

**Key Highlights**:
- All 10 endpoints verified through multiple methods
- CLI command execution confirmed
- Security features verified (no command injection)
- Safe handling of destructive operations

---

## Future Work

### Additional Test Coverage Needed

1. **AI Service Tests** (Phase 6, 8-9, 14 - 9 endpoints)
   - Ideation generation
   - Issue investigation
   - MR review
   - Mock AI responses

3. **Performance Tests**
   - Concurrent file access
   - File locking
   - Large file handling

4. **Load Tests**
   - API rate limiting
   - Concurrent requests
   - Resource usage

## Documentation

For more information:
- **Implementation Plan**: `.aifactory/specs/012-search-this-project-files-for-/test_plan.json`
- **Build Progress**: `.aifactory/specs/012-search-this-project-files-for-/build-progress.txt`
- **File-Based Test Report**: `FILE_BASED_ENDPOINTS_TEST_REPORT.md`
- **CLI Integration Test Report**: `CLI_INTEGRATION_ENDPOINTS_TEST_REPORT.md`

## Contact

For questions about the test infrastructure:
- Review implementation plan (test_plan.json)
- Check build progress logs (build-progress.txt)
- Examine test report (FILE_BASED_ENDPOINTS_TEST_REPORT.md)

---

**Last Updated**: 2026-01-07
**Test Suite Version**: 1.1
**Coverage**: 36/46 endpoints (78.3% of total endpoints)
  - File-based: 26/26 (100%)
  - CLI integration: 10/10 (100%)
  - AI services: 0/9 (pending)
**Status**: ✅ All file-based and CLI integration endpoints verified
