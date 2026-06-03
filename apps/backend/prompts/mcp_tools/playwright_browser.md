## WEB BROWSER VALIDATION

For web frontend applications, use Playwright MCP tools for browser automation and validation.
Playwright runs headless Chromium with automatic management — no manual browser install needed.

### Available Tools

| Tool | Purpose |
|------|---------|
| `mcp__playwright__browser_navigate` | Navigate to URL |
| `mcp__playwright__browser_take_screenshot` | Take screenshot (JPEG, 1280x720) |
| `mcp__playwright__browser_click` | Click element by selector or text |
| `mcp__playwright__browser_fill_form` | Fill input field |
| `mcp__playwright__browser_select_option` | Select dropdown option |
| `mcp__playwright__browser_hover` | Hover over element |
| `mcp__playwright__browser_evaluate` | Execute JavaScript in page context |
| `mcp__playwright__browser_snapshot` | Get accessibility tree snapshot (structured page content) |
| `mcp__playwright__browser_console_messages` | Retrieve browser console messages |
| `mcp__playwright__browser_press_key` | Press keyboard key |
| `mcp__playwright__browser_wait_for` | Wait for selector or navigation |
| `mcp__playwright__browser_navigate_back` | Go back in history |
| `mcp__playwright__browser_close` | Close browser |

### Validation Flow

#### Step 1: Navigate to Page

```
Tool: mcp__playwright__browser_navigate
Args: {"url": "http://localhost:3000"}
```

Navigate to the development server URL.

#### Step 2: Take Screenshot

```
Tool: mcp__playwright__browser_take_screenshot
Args: {}
```

Capture the current page state for visual verification.

#### Step 3: Get Accessibility Snapshot

```
Tool: mcp__playwright__browser_snapshot
Args: {}
```

Get a structured accessibility tree of the page — useful for verifying elements exist without relying on fragile selectors.

#### Step 4: Verify Elements via JavaScript

```
Tool: mcp__playwright__browser_evaluate
Args: {"expression": "document.querySelector('[data-testid=\"feature\"]') !== null"}
```

Check that expected elements are present on the page.

#### Step 5: Test Interactions

**Click buttons/links:**
```
Tool: mcp__playwright__browser_click
Args: {"selector": "[data-testid=\"submit-button\"]"}
```

**Fill form fields:**
```
Tool: mcp__playwright__browser_fill_form
Args: {"selector": "input[name=\"email\"]", "value": "test@example.com"}
```

**Select dropdown options:**
```
Tool: mcp__playwright__browser_select_option
Args: {"selector": "select[name=\"country\"]", "value": "US"}
```

**Press keyboard keys:**
```
Tool: mcp__playwright__browser_press_key
Args: {"key": "Enter"}
```

#### Step 6: Check Console for Errors

```
Tool: mcp__playwright__browser_console_messages
Args: {}
```

Retrieve all console messages (errors, warnings, logs) from the browser.

### Document Findings

```
BROWSER VERIFICATION:
- [Page/Component]: PASS/FAIL
  - Console errors: [list or "None"]
  - Visual check: PASS/FAIL
  - Interactions: PASS/FAIL
  - Accessibility snapshot: PASS/FAIL
```

### Common Selectors

When testing UI elements, prefer these selector strategies:
1. `[data-testid="..."]` - Most reliable (if available)
2. `#id` - Element IDs
3. `text=Button Text` - By visible text (Playwright-native)
4. `.class-name` - CSS classes
5. `input[name="..."]` - Form fields by name
