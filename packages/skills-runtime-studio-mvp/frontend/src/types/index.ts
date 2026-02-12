// Re-export types from api.ts for convenience
export type {
  SkillManifest,
  SkillManifestDependencies,
  Session,
  Run,
  StreamRunEvent,
} from '../lib/api';

// UI-specific types
export type TabId = 'skills' | 'create' | 'run';

export interface TimelineEvent {
  id: string;
  type: string;
  timestamp: Date;
  data: unknown;
  content?: string;
  delta?: string;
}

export interface FilmStripSession {
  id: string;
  label: string;
  timestamp: Date;
  isActive: boolean;
  frameNumber: number;
}
