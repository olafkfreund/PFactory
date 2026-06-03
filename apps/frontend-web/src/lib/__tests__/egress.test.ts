import { describe, it, expect } from 'vitest';
import { classifyEgress } from '../egress';

describe('classifyEgress', () => {
  it('classifies localhost / RFC-1918 / .local as LOCAL', () => {
    expect(classifyEgress('http://localhost:1234')).toBe('local');
    expect(classifyEgress('http://127.0.0.1:11434')).toBe('local');
    expect(classifyEgress('http://192.168.1.50:8080')).toBe('local');
    expect(classifyEgress('http://10.0.0.5')).toBe('local');
    expect(classifyEgress('http://172.16.3.4')).toBe('local');
    expect(classifyEgress('http://ollama.local')).toBe('local');
  });

  it('classifies known third-party APIs as MANAGED_CLOUD', () => {
    expect(classifyEgress('https://api.openai.com/v1')).toBe('managed_cloud');
    expect(classifyEgress('https://openrouter.ai/api/v1')).toBe('managed_cloud');
    expect(classifyEgress('https://generativelanguage.googleapis.com/v1beta/openai')).toBe('managed_cloud');
  });

  it('classifies a routable user host as SELF_HOSTED', () => {
    expect(classifyEgress('https://vllm.mycorp.example.com/v1')).toBe('self_hosted');
    expect(classifyEgress('http://172.40.0.1')).toBe('self_hosted'); // outside 172.16-31
  });

  it('returns null for empty / unparseable input', () => {
    expect(classifyEgress('')).toBeNull();
    expect(classifyEgress(null)).toBeNull();
    expect(classifyEgress('   ')).toBeNull();
  });
});
