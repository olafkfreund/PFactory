import React from 'react';
import { Button } from './button';
import { cn } from '../../lib/utils';

interface StatusBadgeButtonProps {
  iconOnly: boolean;
  icon: React.ReactNode;
  label?: string;
  dotColor: string;
  className?: string;
  children?: React.ReactNode;
}

/**
 * Shared trigger button for status badge popovers.
 * Renders either a compact icon-only button (header) or a full sidebar button
 * with label and optional trailing badges.
 */
export const StatusBadgeButton = React.forwardRef<HTMLButtonElement, StatusBadgeButtonProps>(
  ({ iconOnly, icon, label, dotColor, className, children, ...props }, ref) => {
    if (iconOnly) {
      return (
        <Button
          ref={ref}
          variant="ghost"
          size="icon"
          className={cn('h-8 w-8', className)}
          {...props}
        >
          <div className="relative inline-flex">
            {icon}
            <span className={cn(
              'absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full border border-background',
              dotColor,
            )} />
          </div>
        </Button>
      );
    }

    return (
      <Button
        ref={ref}
        variant="ghost"
        size="sm"
        className={cn('w-full justify-start gap-2 text-xs', className)}
        {...props}
      >
        <div className="relative">
          {icon}
          <span className={cn(
            'absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full',
            dotColor,
          )} />
        </div>
        {label && <span className="truncate">{label}</span>}
        {children}
      </Button>
    );
  }
);

StatusBadgeButton.displayName = 'StatusBadgeButton';
