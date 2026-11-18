import { useQuery } from '@tanstack/react-query';
import { api } from './api';
import type { PlatformInfo } from './api';

import { fallbackPlatform } from './platformInfo';
export { fallbackPlatform } from './platformInfo';

export function usePlatform(): PlatformInfo {
  const query = useQuery({
    queryKey: ['platform'], queryFn: api.platform, staleTime: Infinity, retry: false,
    refetchInterval: (q) => q.state.data ? false : 1500,
  });
  return query.data ?? fallbackPlatform(typeof navigator === 'undefined' ? '' : navigator.userAgent);
}
