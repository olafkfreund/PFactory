#!/usr/bin/env node
/**
 * Cross-platform backend test runner script
 * Runs pytest using the correct virtual environment path for Windows/Mac/Linux
 */

const { execFileSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { assertNotOption } = require('./argv-safety.cjs');

const isWindows = os.platform() === 'win32';
const rootDir = path.join(__dirname, '..');
const backendDir = path.join(rootDir, 'apps', 'backend');
const testsDir = path.join(rootDir, 'tests');
const venvDir = path.join(backendDir, '.venv');

// Get pytest path based on platform
const pytestPath = isWindows
  ? path.join(venvDir, 'Scripts', 'pytest.exe')
  : path.join(venvDir, 'bin', 'pytest');

// Check if venv exists
if (!fs.existsSync(venvDir)) {
  console.error('Error: Virtual environment not found.');
  console.error('Run "npm run install:backend" first.');
  process.exit(1);
}

// Check if pytest is installed
if (!fs.existsSync(pytestPath)) {
  console.error('Error: pytest not found in virtual environment.');
  console.error('Install test dependencies:');
  const pipPath = isWindows
    ? path.join(venvDir, 'Scripts', 'pip.exe')
    : path.join(venvDir, 'bin', 'pip');
  console.error(`  "${pipPath}" install -r tests/requirements-test.txt`);
  process.exit(1);
}

// Get any additional args passed to the script. They are pytest's own flags, so
// they are forwarded verbatim -- but as separate argv entries, not joined into a
// shell string. The old `execSync(\`"${pytestPath}" "${testsDir}" ${testArgs}\`)`
// ran through `sh -c`, so `npm run test:backend -- '; curl evil'` executed
// (js/indirect-command-line-injection). execFileSync spawns pytest directly, so
// nothing here is word-split or expanded.
const args = process.argv.slice(2);
const pytestBin = assertNotOption(pytestPath, 'pytest path');
const pytestArgs = [assertNotOption(testsDir, 'tests dir'), ...(args.length > 0 ? args : ['-v'])];

console.log(`> ${pytestBin} ${pytestArgs.join(' ')}\n`);

try {
  execFileSync(pytestBin, pytestArgs, { stdio: 'inherit', cwd: rootDir });
} catch (error) {
  process.exit(error.status || 1);
}
