import React from 'react';
import './Card.css';

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: 'default' | 'compact' | 'elevated';
  isInteractive?: boolean;
  isActive?: boolean;
  children: React.ReactNode;
}

export const Card: React.FC<CardProps> & {
  Header: typeof CardHeader;
  Content: typeof CardContent;
  Footer: typeof CardFooter;
} = ({
  variant = 'default',
  isInteractive = false,
  isActive = false,
  children,
  className = '',
  ...props
}) => {
  const baseClasses = 'card';
  const variantClass = variant === 'default' ? '' : `card--${variant}`;
  const interactiveClass = isInteractive ? 'card--interactive' : '';
  const activeClass = isActive ? 'card--active' : '';

  const classes = [baseClasses, variantClass, interactiveClass, activeClass, className]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes} {...props}>
      {children}
    </div>
  );
};

// Card Header
interface CardHeaderProps {
  title?: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children?: React.ReactNode;
}

const CardHeader: React.FC<CardHeaderProps> = ({
  title,
  subtitle,
  actions,
  children,
}) => {
  if (children) {
    return <div className="card__header">{children}</div>;
  }

  return (
    <div className="card__header">
      <div>
        {title && <h3 className="card__title">{title}</h3>}
        {subtitle && <p className="card__subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="card__actions">{actions}</div>}
    </div>
  );
};

// Card Content
interface CardContentProps {
  children: React.ReactNode;
  className?: string;
}

const CardContent: React.FC<CardContentProps> = ({ children, className = '' }) => {
  return <div className={`card__content ${className}`}>{children}</div>;
};

// Card Footer
interface CardFooterProps {
  children: React.ReactNode;
  className?: string;
}

const CardFooter: React.FC<CardFooterProps> = ({ children, className = '' }) => {
  return <div className={`card__footer ${className}`}>{children}</div>;
};

// Attach subcomponents
Card.Header = CardHeader;
Card.Content = CardContent;
Card.Footer = CardFooter;
