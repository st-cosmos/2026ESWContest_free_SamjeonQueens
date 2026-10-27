import type { InputHTMLAttributes } from 'react';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  hint?: string;
}

export default function Input({ label, error, hint, className = '', ...rest }: InputProps) {
  return (
    <div className="input-group">
      {label && <span className="input-label">{label}</span>}
      <input className={`input-field ${error ? 'input-error' : ''} ${className}`.trim()} {...rest} />
      {error ? (
        <span className="input-error-msg">{error}</span>
      ) : hint ? (
        <span className="input-hint-msg">{hint}</span>
      ) : null}
    </div>
  );
}
