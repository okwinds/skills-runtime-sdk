import React, { forwardRef, useId } from 'react';
import './Input.css';

export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'prefix'> {
  label?: string;
  error?: string;
  required?: boolean;
  prefix?: React.ReactNode;
  suffix?: React.ReactNode;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, required, prefix, suffix, className = '', id, ...props }, ref) => {
    const autoId = useId();
    const inputId = id ?? `input-${autoId}`;
    const wrapperClass = error ? 'input-affix-wrapper input-affix-wrapper--error' : 'input-affix-wrapper';
    const inputClass = `input-field ${error ? 'input-field--error' : ''} ${className}`;

    return (
      <div className="input-wrapper">
        {label && (
          <label
            htmlFor={inputId}
            className={`input-label ${required ? 'input-label--required' : ''}`}
          >
            {label}
          </label>
        )}

        {prefix || suffix ? (
          <div className={wrapperClass}>
            {prefix && <span className="input-prefix">{prefix}</span>}
            <input
              ref={ref}
              id={inputId}
              className={inputClass}
              required={required}
              {...props}
            />
            {suffix && <span className="input-suffix">{suffix}</span>}
          </div>
        ) : (
          <input
            ref={ref}
            id={inputId}
            className={inputClass}
            required={required}
            {...props}
          />
        )}

        {error && (
          <span className="input-error" role="alert">
            <svg
              className="input-error-icon"
              viewBox="0 0 12 12"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <circle cx="6" cy="6" r="5.5" stroke="currentColor" />
              <path d="M6 3v3.5" stroke="currentColor" strokeLinecap="round" />
              <circle cx="6" cy="8.5" r="0.5" fill="currentColor" />
            </svg>
            {error}
          </span>
        )}
      </div>
    );
  }
);

Input.displayName = 'Input';

// Textarea component
export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
  required?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ label, error, required, className = '', id, ...props }, ref) => {
    const autoId = useId();
    const textareaId = id ?? `textarea-${autoId}`;
    const textareaClass = `input-field ${error ? 'input-field--error' : ''} ${className}`;

    return (
      <div className="input-wrapper">
        {label && (
          <label
            htmlFor={textareaId}
            className={`input-label ${required ? 'input-label--required' : ''}`}
          >
            {label}
          </label>
        )}
        <textarea
          ref={ref}
          id={textareaId}
          className={textareaClass}
          required={required}
          {...props}
        />
        {error && (
          <span className="input-error" role="alert">
            {error}
          </span>
        )}
      </div>
    );
  }
);

Textarea.displayName = 'Textarea';
