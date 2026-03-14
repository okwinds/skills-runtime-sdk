import React from 'react';
import './SkillList.css';
import type { SkillManifest } from '../../types';

interface SkillListProps {
  skills: SkillManifest[];
  isLoading?: boolean;
}

const getSkillIcon = (enabled: boolean): React.ReactNode => {
  if (!enabled) {
    return (
      <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
        <path d="M12 2a10 10 0 100 20 10 10 0 000-20zm5 13.6L8.4 7 7 8.4 15.6 17 17 15.6z" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
      <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
    </svg>
  );
};

export const SkillList: React.FC<SkillListProps> = ({ skills, isLoading }) => {
  if (isLoading) {
    return (
      <div className="skill-list">
        <div className="skill-list__header">
          <h2 className="skill-list__title">Skills</h2>
          <span className="skill-list__count">...</span>
        </div>
        <div className="skill-list__grid">
          {[1, 2, 3].map((i) => (
            <div key={i} className="skill-card skill-card--skeleton">
              <div className="skill-card__header">
                <div className="skill-card__icon" />
                <div className="skill-card__version" />
              </div>
              <div className="skill-card__name" />
              <div className="skill-card__description" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="skill-list">
      <div className="skill-list__header">
        <h2 className="skill-list__title">Skills</h2>
        <span className="skill-list__count">{skills.length} total</span>
      </div>

      {skills.length === 0 ? (
        <div className="skill-list__empty">
          <p className="skill-list__empty-title">No skills yet</p>
          <p className="skill-list__empty-desc">Create your first skill to get started</p>
        </div>
      ) : (
        <div className="skill-list__grid">
          {skills.map((skill) => (
            <div key={skill.path || skill.name} className="skill-card">
              <div className="skill-card__header">
                <div className="skill-card__icon">{getSkillIcon(skill.enabled)}</div>
                <span className="skill-card__version">{skill.enabled ? 'ENABLED' : 'DISABLED'}</span>
              </div>

              <h3 className="skill-card__name">{skill.name}</h3>
              <p className="skill-card__description">{skill.description}</p>

              <div className="skill-card__footer">
                <span className="skill-card__meta">{skill.path}</span>
                <span className="skill-card__entry-type">
                  env:{skill.dependencies.env_vars.length}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

