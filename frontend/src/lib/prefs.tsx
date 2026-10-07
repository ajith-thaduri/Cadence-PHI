// Per-browser preferences (view mode, value masking). Stored in localStorage when available; the console works
// the same without it. Never used for anything that must persist or be shared.
import { createContext, useContext, useState, type ReactNode } from 'react';

export interface Prefs {
  technical: boolean;
  maskValues: boolean;
}

const DEFAULTS: Prefs = { technical: false, maskValues: false };
const KEY = 'phi-console-prefs';

function read(): Prefs {
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? { ...DEFAULTS, ...(JSON.parse(raw) as Partial<Prefs>) } : DEFAULTS;
  } catch {
    return DEFAULTS;
  }
}

const Ctx = createContext<{ prefs: Prefs; set: (p: Partial<Prefs>) => void }>({
  prefs: DEFAULTS,
  set: () => {},
});

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<Prefs>(read);
  const set = (p: Partial<Prefs>) =>
    setPrefs((cur) => {
      const next = { ...cur, ...p };
      try {
        window.localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable: keep in memory */
      }
      return next;
    });
  return <Ctx.Provider value={{ prefs, set }}>{children}</Ctx.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export const usePrefs = () => useContext(Ctx);
