# Phase Transition Fix - Testing Guide

## What Was Fixed

**Problem:** Tasks got stuck at "In Progress" after plan creation, instead of transitioning to "Human Review" phase.

**Root Cause:** spec_runner.py exits with code 1 when waiting for human approval, which agent_service.py interpreted as a failure.

**Solution:** Added review state detection in agent_service.py to differentiate between:
- **Waiting for review** (exit code 1 + review_state.json with approved=false) → PLAN_REVIEW phase
- **Actual failure** (exit code 1 + other conditions) → FAILED phase

## Changes Made

### File Modified
- `apps/web-server/server/services/agent_service.py`

### Code Added (lines 962-1014)
```python
# Check if exit code 1 is actually "waiting for review" not a failure
if return_code != 0 and project_path and spec_id:
    spec_dir = project_path / ".pfactory" / "specs" / spec_id
    review_state_file = spec_dir / "review_state.json"

    # If review_state.json exists with approved=false, task is waiting for human review
    if review_state_file.exists():
        try:
            review_data = json.loads(review_state_file.read_text())
            if not review_data.get("approved", False):
                # This is NOT a failure - it's waiting for human review!
                # ... (cleanup and transition to PLAN_REVIEW phase)
                return  # Exit early - not a failure
        except (json.JSONDecodeError, OSError) as e:
            # Fall through to treat as actual failure
            pass
```

## How to Test

### Test Case 1: Task Requiring Human Review

**Setup:**
1. Create a new task with "Require review before coding" enabled
2. Start the task

**Expected Behavior:**
- ✅ Task creates spec.md and test_plan.json
- ✅ Task transitions to **"Human Review"** phase (not "Failed")
- ✅ Status updates automatically without page refresh
- ✅ Task appears in "Human Review" column on kanban board
- ✅ Message: "Plan created - waiting for human approval"

**Before Fix:**
- ❌ Task showed as "Failed"
- ❌ Required page refresh to see status
- ❌ Appeared stuck at "In Progress"

### Test Case 2: Task with Auto-Approve

**Setup:**
1. Create a task with "Require review before coding" disabled (auto-approve)
2. Start the task

**Expected Behavior:**
- ✅ Task creates spec and continues to CODING phase automatically
- ✅ No human review required
- ✅ Progresses through: PLANNING → CODING → QA_REVIEW → COMPLETED

### Test Case 3: Actual Task Failure

**Setup:**
1. Create a task that will genuinely fail (e.g., invalid config, network error)
2. Start the task

**Expected Behavior:**
- ✅ Task transitions to **"Failed"** phase
- ✅ Error message displayed
- ✅ Status updates to "QA Failed" or appropriate failure state

### Test Case 4: Task Completion

**Setup:**
1. Create and complete a task successfully
2. Let it run to completion

**Expected Behavior:**
- ✅ Task transitions to COMPLETED phase
- ✅ Status updates to "human_review" (completed tasks)
- ✅ Appears in "Human Review" column for final approval

## Manual Testing Steps

### Step 1: Restart Servers
```bash
# Kill existing servers
fuser -k 3115/tcp 3114/tcp

# Start backend
cd apps/web-server
source .venv/bin/activate
python -m server.main

# Start frontend (in another terminal)
cd apps/frontend-web
npm run dev
```

### Step 2: Create Test Task
1. Open web UI: http://localhost:3115
2. Click "New Task"
3. Fill in:
   - Title: "Test Phase Transition Fix"
   - Description: "Simple task to test review phase"
   - **Enable**: "Require review before coding"
4. Click "Create Task"

### Step 3: Observe Behavior
Watch the task progress:
1. **Phase 1**: "Spec Creation" (creating spec.md)
2. **Phase 2**: Should transition to **"Human Review"** (NOT "Failed")
3. **Kanban**: Task should appear in "Human Review" column
4. **No page refresh needed** - updates in real-time

### Step 4: Approve and Continue
1. Click "Approve" on the task
2. Task should continue to CODING phase
3. Watch it progress through remaining phases

## Verification Checklist

- [ ] Task transitions to "Human Review" when spec is complete
- [ ] Status updates automatically (no refresh needed)
- [ ] WebSocket events emit correctly
- [ ] Task appears in correct kanban column
- [ ] Log shows: "Task {id} awaiting human review (not a failure)"
- [ ] Log shows: "Task {id} transitioned to PLAN_REVIEW phase"
- [ ] No "failed" status when waiting for review
- [ ] Auto-approve mode still works (skips review)
- [ ] Actual failures still show as "Failed"

## Server Logs to Check

**Success indicators:**
```
[AgentService] Task {id} process exited with code 1
[AgentService] Task {id} awaiting human review (not a failure)
[AgentService] Updated plan status to 'human_review' for {spec_id}
[AgentService] Task {id} transitioned to PLAN_REVIEW phase
```

**Failure case (for comparison):**
```
[AgentService] Task {id} process exited with code 1
[AgentService] Task {id} failed with exit code 1
```

## Expected Files

After spec creation, the following files should exist:
```
.pfactory/specs/{spec-id}/
├── spec.md                      ← Spec document
├── test_plan.json     ← Implementation plan
├── requirements.json            ← Requirements
├── context.json                 ← Context data
├── review_state.json            ← Review state (approved: false)
└── task_metadata.json           ← Task metadata
```

The key file is `review_state.json`:
```json
{
  "approved": false,
  "approved_by": "",
  "approved_at": "",
  "feedback": [],
  "spec_hash": "",
  "review_count": 0
}
```

## Troubleshooting

### Task still shows as "Failed"
- Check server logs for the new log messages
- Verify review_state.json exists with approved=false
- Restart backend server to load the new code

### WebSocket not updating
- Hard refresh browser (Ctrl+Shift+R)
- Check browser console for WebSocket errors
- Verify WebSocket connection in Network tab

### Phase doesn't transition
- Check that emit_task_progress is called
- Verify phase mapping: PLAN_REVIEW → "human_review"
- Check test_plan.json status field

## Rollback Plan

If the fix causes issues, revert the change:
```bash
cd apps/web-server/server/services
git diff agent_service.py  # Review changes
git checkout agent_service.py  # Revert if needed
```

The fix is isolated to one location (agent_service.py:962-1014) and can be safely reverted.

## Success Criteria

✅ **Fix is successful if:**
1. Tasks requiring review transition to "Human Review" phase
2. Status updates happen in real-time (no refresh needed)
3. Auto-approve mode continues to work
4. Actual failures still show as "Failed"
5. No regressions in other phase transitions

## Next Steps

After verification:
1. ✅ Update CHANGELOG.md with fix details
2. ✅ Create GitHub issue documenting the bug and fix
3. ✅ Add automated test for phase transition logic
4. ✅ Consider emitting phase events from spec_runner.py for clarity
