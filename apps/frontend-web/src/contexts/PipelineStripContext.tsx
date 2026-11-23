import { createContext, useContext } from 'react';
import type { PipelineStripProps } from '../components/factory-ui/PipelineStrip';

export interface PipelineStripContextValue {
  setStrip: (strip: PipelineStripProps | undefined) => void;
}

export const PipelineStripContext = createContext<PipelineStripContextValue | null>(null);

export function usePipelineStripSlot(): PipelineStripContextValue | null {
  return useContext(PipelineStripContext);
}
