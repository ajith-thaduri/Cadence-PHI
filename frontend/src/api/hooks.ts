import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type {
  AnalyzeIn,
  AnalyzeOut,
  AuditRow,
  CanaryReport,
  ConfigView,
  Dataset,
  DocIndexItem,
  GatesLatest,
  Job,
  JobType,
  LedgerRow,
  MonitoringView,
  RestoreOut,
  RunDetail,
  RunListItem,
  SessionInfo,
  Status,
  ValidationResult,
} from './types';

const ACTIVE = new Set(['queued', 'running']);

export const useSession = () =>
  useQuery({ queryKey: ['session'], queryFn: () => api.get<SessionInfo>('/session'), staleTime: 60_000 });

export const useStatus = () =>
  useQuery({
    queryKey: ['status'],
    queryFn: () => api.get<Status>('/status'),
    refetchInterval: (q) => (q.state.data?.engine.ready ? 30_000 : 3_000),
  });

export const useConfig = () =>
  useQuery({ queryKey: ['config'], queryFn: () => api.get<ConfigView>('/config'), staleTime: 300_000 });

export const useResolvedConfig = (enabled: boolean) =>
  useQuery({
    queryKey: ['config', 'resolved'],
    queryFn: () => api.get<{ yaml: string }>('/config/resolved'),
    enabled,
  });

export const useRuns = (kind?: string) =>
  useQuery({
    queryKey: ['runs', kind ?? 'all'],
    queryFn: () => api.get<RunListItem[]>(kind ? `/runs?kind=${encodeURIComponent(kind)}` : '/runs'),
  });

export const useRun = (kind: string, name: string) =>
  useQuery({
    queryKey: ['run', kind, name],
    queryFn: () => api.get<RunDetail>(`/runs/${encodeURIComponent(kind)}/${encodeURIComponent(name)}`),
  });

export const useGates = () =>
  useQuery({ queryKey: ['gates'], queryFn: () => api.get<GatesLatest | null>('/gates/latest') });

export const useJobTypes = () =>
  useQuery({
    queryKey: ['job-types'],
    queryFn: () => api.get<{ types: JobType[]; datasets: Dataset[] }>('/job-types'),
    staleTime: 300_000,
  });

export const useJobs = () =>
  useQuery({
    queryKey: ['jobs'],
    queryFn: () => api.get<Job[]>('/jobs'),
    refetchInterval: (q) => (q.state.data?.some((j) => ACTIVE.has(j.status)) ? 2_000 : 15_000),
  });

export const useJob = (id: string | null) =>
  useQuery({
    queryKey: ['job', id],
    queryFn: () => api.get<Job>(`/jobs/${id}`),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.status) ? 1_000 : false),
  });

/** Follows a job log incrementally (offset-based polling) while the job is active. */
export function useJobLog(id: string | null) {
  // keyed by job id, so switching jobs never shows the previous job's log
  const [log, setLog] = useState<{ id: string | null; text: string }>({ id: null, text: '' });
  const offset = useRef(0);
  useEffect(() => {
    offset.current = 0;
    if (!id) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const r = await api.get<{ text: string; offset: number; status: string }>(
          `/jobs/${id}/log?offset=${offset.current}`,
        );
        if (stop) return;
        if (r.text) setLog((l) => ({ id, text: ((l.id === id ? l.text : '') + r.text).slice(-200_000) }));
        offset.current = r.offset;
        if (ACTIVE.has(r.status) || r.text) timer = setTimeout(tick, 1_000);
      } catch {
        if (!stop) timer = setTimeout(tick, 3_000);
      }
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [id]);
  return log.id === id ? log.text : '';
}

export const useMonitoring = () =>
  useQuery({
    queryKey: ['monitoring'],
    queryFn: () => api.get<MonitoringView>('/monitoring'),
    refetchInterval: 30_000,
  });

export const useDocsIndex = () =>
  useQuery({ queryKey: ['docs'], queryFn: () => api.get<DocIndexItem[]>('/docs-index'), staleTime: 300_000 });

export const useDoc = (slug: string) =>
  useQuery({
    queryKey: ['doc', slug],
    queryFn: () => api.get<{ slug: string; path: string; markdown: string }>(`/docs-page/${slug}`),
    enabled: !!slug,
  });

export const useLedger = () =>
  useQuery({ queryKey: ['ledger'], queryFn: () => api.get<LedgerRow[]>('/ledger') });

export const useAudit = (enabled: boolean) =>
  useQuery({ queryKey: ['audit'], queryFn: () => api.get<AuditRow[]>('/audit'), enabled });

// ---------------------------------------------------------------------------- mutations
export const useAnalyze = () =>
  useMutation({ mutationFn: (body: AnalyzeIn) => api.post<AnalyzeOut>('/analyze', body) });

export const useRestore = () =>
  useMutation({
    mutationFn: (body: { vault_id: string; text: string }) => api.post<RestoreOut>('/restore', body),
  });

export function useSubmitJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { type: string; params: Record<string, unknown>; reason?: string }) =>
      api.post<Job>('/jobs', body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['jobs'] }),
  });
}

export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<Job>(`/jobs/${id}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['jobs'] }),
  });
}

export function useBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (form: FormData) => api.post<Job>('/batch', form),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['jobs'] }),
  });
}

export const useBatchSummary = (id: string | null, ready: boolean) =>
  useQuery({
    queryKey: ['batch-summary', id],
    queryFn: () => api.get<{ rows: Record<string, string>[] }>(`/jobs/${id}/summary`),
    enabled: !!id && ready,
  });

export function useRunCanaries() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<CanaryReport>('/monitoring/canaries'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitoring'] }),
  });
}

export const useValidateConfig = () =>
  useMutation({ mutationFn: () => api.post<ValidationResult[]>('/config/validate') });

export function useReloadEngine() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ config_sha256: string }>('/engine/reload'),
    onSuccess: () => qc.invalidateQueries(),
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { username: string; secret: string }) => api.post('/session', body),
    onSuccess: () => qc.invalidateQueries(),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.del('/session'),
    onSuccess: () => {
      qc.clear();
      window.location.assign('/');
    },
  });
}
