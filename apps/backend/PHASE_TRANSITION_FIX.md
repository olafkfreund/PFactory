# Phase Transition Issue - Root Cause & Fix

## Problem Description

**Symptoms:**
- Task gets stuck at "In Progress" status after plan creation
- UI shows "Plan created - waiting for human approval" but status doesn't update to "human_review"
- Requires page refresh to see the correct phase

**Reported Example:**
- Task 022-git-history-versions
- Time: 11:14:26 PM
- Phase stayed "In Progress" instead of transitioning to "Human Review"

## Root Cause Analysis

### The Flow

1. **spec_runner.py completes** - Creates spec.md and test_plan.json
2. **Auto-start build check** (spec_runner.py:287-308):
   ```python
   if not args.no_build:
       review_state = ReviewState.load(orchestrator.spec_dir)
       if not review_state.is_approved():
           print("Build cannot start: spec not approved.")
           sys.exit(1)  # ← PROBLEM: Exit code 1 = failure
   ```

3. **Agent service detects exit** (agent_service.py:960):
   ```python
   return_code = await asyncio.wait_for(proc.wait(), timeout=sync_interval)
   # return_code = 1
   ```

4. **Treats as failure** (agent_service.py:1076-1084):
   ```python
   if return_code != 0:
       # Emits FAILED phase, not PLAN_REVIEW
       await self._emit_progress(
           TaskProgress(..., phase=TaskPhase.FAILED, ...)
       )
   ```

### The Bug

**spec_runner.py exits with code 1 when waiting for review**, which agent_service.py interprets as a failure, not as "waiting for human review".

The correct flow should be:
- Spec created → **PLAN_REVIEW phase** → human approves → continues to CODING

But it actually does:
- Spec created → **FAILED phase** (exit code 1) → stuck

## Solution

Add review state detection in `agent_service.py` to differentiate between:
- **True failure** (exit code 1, no review needed)
- **Waiting for review** (exit code 1, review_state.json exists with approved=false)

### Implementation

Modify `agent_service.py:_monitor_process()` around line 960 to check review state:

```python
# After process exits
if return_code != 0:
    # Check if this is "waiting for review" vs actual failure
    if project_path and spec_id:
        spec_dir = project_path / ".pfactory" / "specs" / spec_id
        review_state_file = spec_dir / "review_state.json"

        # If review_state.json exists and approved=false, this is PLAN_REVIEW phase
        if review_state_file.exists():
            try:
                import json
                review_state = json.loads(review_state_file.read_text())
                if not review_state.get("approved", False):
                    # Waiting for human review - not a failure!
                    logger.info(f"[AgentService] Task {task_id} awaiting human review")

                    # Emit PLAN_REVIEW phase instead of FAILED
                    await self._emit_progress(
                        TaskProgress(
                            task_id=task_id,
                            phase=TaskPhase.PLAN_REVIEW,
                            message="Plan created - waiting for human approval",
                        ),
                        previous_phase=actual_phase,
                    )

                    # Update plan status to human_review
                    await self._update_plan_status(project_path, spec_id, "human_review", task_id)
                    return  # Exit early - not a failure
            except (json.JSONDecodeError, OSError):
                pass  # Fall through to treat as actual failure

    # If we get here, it's an actual failure
    logger.error(f"[AgentService] Task {task_id} failed with exit code {return_code}")
    # ... existing failure handling
```

## Benefits of This Fix

1. **No CLI changes needed** - spec_runner.py exit codes unchanged
2. **Backward compatible** - Doesn't break existing behavior
3. **Correct phase tracking** - Tasks properly move to "Human Review" column
4. **Real-time updates** - WebSocket event triggered immediately
5. **File sync** - Works with existing worktree sync mechanism

## Testing

Test cases:
1. ✓ Task with auto-approve → goes directly to CODING
2. ✓ Task requiring review → transitions to PLAN_REVIEW (human_review status)
3. ✓ Task failure (real error) → transitions to FAILED
4. ✓ Task completion → transitions to COMPLETED (human_review status)

## Files Modified

- `apps/web-server/server/services/agent_service.py` - Add review state detection in `_monitor_process()`

## Alternative Solutions Considered

### Option 1: Change spec_runner.py exit codes
- spec_runner.py: Use exit code 2 for "waiting for review"
- agent_service.py: Recognize code 2 as PLAN_REVIEW phase
- **Rejected**: Requires changing CLI behavior, less maintainable

### Option 2: Emit phase event before exit
- spec_runner.py: Emit `__EXEC_PHASE__:{"phase":"plan_review"}` before sys.exit(1)
- agent_service.py: Captures event and transitions phase
- **Rejected**: Requires modifying output parsing, fragile

### Option 3: Check ReviewState on exit (CHOSEN)
- agent_service.py: Check review_state.json when process exits with code 1
- If approved=false, treat as PLAN_REVIEW not FAILED
- **Chosen**: Clean, no CLI changes, uses existing state files

## Related Issues

- File sync mechanism works correctly (periodic 3-second sync)
- WebSocket emission works correctly (emit_task_status)
- Phase mapping works correctly (PLAN_REVIEW → "human_review")

The only issue was the exit code interpretation.
