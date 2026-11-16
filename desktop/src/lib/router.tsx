// A router, in the amount of routing this app actually does.
//
// This replaces react-router-dom, which was carrying three moderate
// advisories (two open-redirect, one SSR-hydration `deserializeErrors`)
// that `npm audit fix` could only resolve with a v7 major bump. None of them
// were reachable here — there is no SSR, every navigation target in the app
// is a hardcoded literal, and a Tauri webview under a strict CSP has nowhere
// to be redirected *to*. But "not exploitable today" is a fragile position to
// ship a security-focused product on, and the alternative to a risky major
// bump was noticing how little of the library was in use: three static
// routes, no params, no nested routes, no loaders, no data APIs.
//
// So: ~40 lines, no dependency, nothing to audit. History is still driven
// through `pushState`/`popstate` so the webview's back gesture keeps working.

import { createContext, ReactNode, useContext, useEffect, useState } from 'react';

type RouterValue = { path: string; navigate: (to: string) => void };

const RouterContext = createContext<RouterValue>({ path: '/', navigate: () => {} });

function currentPath(): string {
  // Tauri serves the UI from a custom protocol where `pathname` can be a file
  // path rather than a route, so anything that is not one of ours is home.
  const p = window.location.pathname;
  return p.startsWith('/') && !p.includes('.') ? p : '/';
}

export function Router({ children }: { children: ReactNode }) {
  const [path, setPath] = useState(currentPath);

  useEffect(() => {
    const onPop = () => setPath(currentPath());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = (to: string) => {
    if (to === path) return;
    window.history.pushState({}, '', to);
    setPath(to);
  };

  return <RouterContext.Provider value={{ path, navigate }}>{children}</RouterContext.Provider>;
}

export function useRouter(): RouterValue {
  return useContext(RouterContext);
}

export function useNavigate(): (to: string) => void {
  return useContext(RouterContext).navigate;
}

/** A nav item that knows whether it is the current route. */
export function NavItem({
  to,
  className,
  children,
  ...rest
}: {
  to: string;
  className: (active: boolean) => string;
  children: ReactNode;
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'className'>) {
  const { path, navigate } = useRouter();
  return (
    <button className={className(path === to)} onClick={() => navigate(to)} {...rest}>
      {children}
    </button>
  );
}
