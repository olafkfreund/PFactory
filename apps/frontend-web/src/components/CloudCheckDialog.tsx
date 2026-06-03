/**
 * CloudCheckDialog (#133) — launch a cloud infrastructure check from the portal.
 *
 * Models the intended flow: run the read-only access/discovery **gate** first
 * ("do we get in, and what's here?"); only if access is granted does the server
 * background the Prowler assessment, whose report then appears in Cloud Reports.
 */
import { useState } from 'react';
import { Cloud, Loader2, ShieldCheck, ShieldAlert } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { post } from '../lib/api-client';

type Provider = 'aws' | 'gcp' | 'azure';

interface GateResult {
  gate: 'ok' | 'no_access';
  provider: string;
  account?: string | null;
  identity?: string | null;
  inventory?: { global?: Record<string, Record<string, unknown>> };
  error?: string | null;
  status?: string;
}

// Per-provider label for the single "target" field (profile vs project vs sub).
const TARGET_LABEL: Record<Provider, string> = {
  aws: 'AWS profile (e.g. Calitii)',
  gcp: 'GCP project id (e.g. sarc-493418)',
  azure: 'Azure subscription id (optional)',
};

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after a check is successfully launched, so the report list can refresh. */
  onLaunched?: () => void;
}

export function CloudCheckDialog({ open, onOpenChange, onLaunched }: Props) {
  const [provider, setProvider] = useState<Provider>('aws');
  const [target, setTarget] = useState('');
  const [services, setServices] = useState('');
  const [failOn, setFailOn] = useState('high');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<GateResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setResult(null);
    setError(null);
    setBusy(false);
  };

  const close = (o: boolean) => {
    if (!o) reset();
    onOpenChange(o);
  };

  const launch = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    const body = {
      provider,
      profile: target.trim() || null,
      services: services.split(',').map((s) => s.trim()).filter(Boolean),
      fail_on_severity: failOn,
    };
    const res = await post<GateResult>('/cloud/assessments/run', body);
    setBusy(false);
    if (!res.success || !res.data) {
      setError(res.error || 'Request failed');
      return;
    }
    setResult(res.data);
    if (res.data.gate === 'ok') onLaunched?.();
  };

  const inventoryBits = (inv?: GateResult['inventory']): string => {
    const g = inv?.global || {};
    return (
      Object.entries(g)
        .map(([k, v]) => {
          const count = (v?.count as number) ?? Object.entries(v || {})[0]?.[1];
          return `${k.replace(/_/g, ' ')}: ${count}`;
        })
        .join(' · ') || 'no global resources enumerated'
    );
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Cloud className="h-5 w-5" /> Cloud Infrastructure check
          </DialogTitle>
          <DialogDescription>
            Read-only. We check access &amp; discover what's there first; if we get in, the
            assessment runs in the background and its report appears in Cloud Reports.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Provider</label>
            <select
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={provider}
              onChange={(e) => {
                setProvider(e.target.value as Provider);
                reset();
              }}
            >
              <option value="aws">AWS</option>
              <option value="gcp">Google Cloud</option>
              <option value="azure">Azure</option>
            </select>
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium">{TARGET_LABEL[provider]}</label>
            <Input
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder={provider === 'azure' ? 'leave blank for active login' : ''}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Services (optional)</label>
              <Input
                value={services}
                onChange={(e) => setServices(e.target.value)}
                placeholder="iam, storage"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Fail on severity</label>
              <select
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={failOn}
                onChange={(e) => setFailOn(e.target.value)}
              >
                <option value="critical">Critical</option>
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>
            </div>
          </div>

          {error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
              {error}
            </div>
          )}

          {result?.gate === 'no_access' && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
              <div>
                <p className="font-medium text-destructive">No access — check skipped</p>
                <p className="text-muted-foreground">{result.error || 'credentials did not grant access'}</p>
              </div>
            </div>
          )}

          {result?.gate === 'ok' && (
            <div className="flex items-start gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
              <div>
                <p className="font-medium text-emerald-700 dark:text-emerald-400">
                  Access confirmed — assessment running
                </p>
                <p className="text-muted-foreground">
                  {result.identity ? `${result.identity} · ` : ''}account {result.account}
                </p>
                <p className="text-muted-foreground">Found — {inventoryBits(result.inventory)}.</p>
                <p className="mt-1 text-muted-foreground">
                  The report will appear in <strong>Cloud Reports</strong> when the scan finishes.
                </p>
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          {result?.gate === 'ok' ? (
            <Button onClick={() => close(false)}>Done</Button>
          ) : (
            <>
              <Button variant="outline" onClick={() => close(false)} disabled={busy}>
                Cancel
              </Button>
              <Button onClick={launch} disabled={busy}>
                {busy ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Checking access…
                  </>
                ) : (
                  'Check access & run'
                )}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
