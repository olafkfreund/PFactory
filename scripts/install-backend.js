#!/usr/bin/env node
/**
 * Cross-platform backend installer script
 * Handles Python venv creation and dependency installation on Windows/Mac/Linux
 */

const { execSync, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

const isWindows = os.platform() === 'win32';
const backendDir = path.join(__dirname, '..', 'apps', 'backend');
const venvDir = path.join(backendDir, '.venv');

console.log('Installing Magestic AI backend dependencies...\n');

// Helper to run commands
function run(cmd, options = {}) {
  console.log(`> ${cmd}`);
  try {
    execSync(cmd, { stdio: 'inherit', cwd: backendDir, ...options });
    return true;
  } catch (error) {
    return false;
  }
}

// Find Python 3.12+
// Prefer 3.12 first since it has the most stable wheel support for native packages
function findPython() {
  const candidates = isWindows
    ? ['py -3.12', 'py -3.13', 'py -3.14', 'python3.12', 'python3.13', 'python3.14', 'python3', 'python']
    : ['python3.12', 'python3.13', 'python3.14', 'python3', 'python'];

  for (const cmd of candidates) {
    try {
      const result = spawnSync(cmd.split(' ')[0], [...cmd.split(' ').slice(1), '--version'], {
        encoding: 'utf8',
        shell: true,
      });
      // Accept Python 3.12+ using proper version parsing
      if (result.status === 0) {
        const versionMatch = result.stdout.match(/Python (\d+)\.(\d+)/);
        if (versionMatch) {
          const major = parseInt(versionMatch[1], 10);
          const minor = parseInt(versionMatch[2], 10);
          if (major === 3 && minor >= 12) {
            console.log(`Found Python 3.12+: ${cmd} -> ${result.stdout.trim()}`);
            return cmd;
          }
        }
      }
    } catch (e) {
      // Continue to next candidate
    }
  }
  return null;
}

// Get pip path based on platform
function getPipPath() {
  return isWindows
    ? path.join(venvDir, 'Scripts', 'pip.exe')
    : path.join(venvDir, 'bin', 'pip');
}

// Main installation
async function main() {
  // Check for Python 3.12+
  const python = findPython();
  if (!python) {
    console.error('\nError: Python 3.12+ is required but not found.');
    console.error('Please install Python 3.12 or higher:');
    if (isWindows) {
      console.error('  winget install Python.Python.3.12');
    } else if (os.platform() === 'darwin') {
      console.error('  brew install python@3.12');
    } else {
      console.error('  sudo apt install python3.12 python3.12-venv');
    }
    process.exit(1);
  }

  // Remove existing venv if present
  if (fs.existsSync(venvDir)) {
    console.log('\nRemoving existing virtual environment...');
    fs.rmSync(venvDir, { recursive: true, force: true });
  }

  // Create virtual environment
  console.log('\nCreating virtual environment...');
  if (!run(`${python} -m venv .venv`)) {
    console.error('Failed to create virtual environment');
    process.exit(1);
  }

  // Install dependencies
  console.log('\nInstalling dependencies...');
  const pip = getPipPath();
  if (!run(`"${pip}" install -r requirements.txt`)) {
    console.error('Failed to install dependencies');
    process.exit(1);
  }

  // Preinstall @google/gemini-cli (Antigravity CLI) per default
  console.log('\nPreinstalling @google/gemini-cli (Antigravity CLI) per default...');
  try {
    const installDir = path.join(os.homedir(), '.gemini', 'antigravity-cli');
    fs.mkdirSync(path.join(os.homedir(), '.gemini'), { recursive: true });
    
    // Check if npm is installed
    try {
      execSync('npm --version', { stdio: 'ignore' });
      console.log(`Installing @google/gemini-cli to ${installDir}...`);
      execSync(`npm install -g --prefix "${installDir}" @google/gemini-cli`, { stdio: 'inherit' });
      
      // Create symlink from antigravity -> gemini
      const binDir = path.join(installDir, 'bin');
      const symlinkPath = path.join(binDir, 'antigravity');
      const targetPath = path.join(binDir, 'gemini');
      
      if (fs.existsSync(targetPath)) {
        if (fs.existsSync(symlinkPath) || fs.lstatSync(symlinkPath).isSymbolicLink()) {
          fs.unlinkSync(symlinkPath);
        }
        fs.symlinkSync('gemini', symlinkPath);
        console.log('Successfully created antigravity -> gemini symlink.');
      }
    } catch (npmErr) {
      console.log('npm is not installed or failed to execute. Skipping @google/gemini-cli preinstallation.');
    }
  } catch (err) {
    console.log(`Warning: Failed to preinstall @google/gemini-cli: ${err.message}. You can still install it later via settings.`);
  }

  console.log('\nBackend installation complete!');
  console.log(`Virtual environment: ${venvDir}`);
}

main().catch((err) => {
  console.error('Installation failed:', err);
  process.exit(1);
});
