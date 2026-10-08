import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent, type ReactNode } from "react";

// Enrutador mínimo sobre la History API: evita una dependencia más para cuatro pantallas.
const NAVIGATE_EVENT = "aleph:navigate";

export interface Location {
  pathname: string;
  search: string;
}

function currentLocation(): Location {
  return { pathname: window.location.pathname, search: window.location.search };
}

export function navigate(to: string, options: { replace?: boolean } = {}): void {
  if (options.replace) window.history.replaceState({}, "", to);
  else window.history.pushState({}, "", to);
  window.dispatchEvent(new Event(NAVIGATE_EVENT));
}

export function useLocation(): Location {
  const [location, setLocation] = useState<Location>(currentLocation);
  useEffect(() => {
    const update = () => setLocation(currentLocation());
    window.addEventListener("popstate", update);
    window.addEventListener(NAVIGATE_EVENT, update);
    return () => {
      window.removeEventListener("popstate", update);
      window.removeEventListener(NAVIGATE_EVENT, update);
    };
  }, []);
  return location;
}

/** Devuelve los parámetros del patrón (`/casos/:id`) o null si no coincide. */
export function matchPath(pattern: string, pathname: string): Record<string, string> | null {
  const patternParts = pattern.split("/").filter(Boolean);
  const pathParts = pathname.split("/").filter(Boolean);
  if (patternParts.length !== pathParts.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < patternParts.length; i += 1) {
    const expected = patternParts[i];
    const actual = pathParts[i];
    if (expected.startsWith(":")) {
      params[expected.slice(1)] = decodeURIComponent(actual);
    } else if (expected !== actual) {
      return null;
    }
  }
  return params;
}

interface LinkProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> {
  to: string;
  children: ReactNode;
}

export function Link({ to, children, onClick, ...rest }: LinkProps) {
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(to);
  };
  return (
    <a href={to} onClick={handleClick} {...rest}>
      {children}
    </a>
  );
}
