interface IconProps {
  className?: string;
}

export function AnthropicIcon({ className = 'h-4 w-4' }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" fill="currentColor">
      <path d="M13.827 3.52h3.603L24 20.48h-3.603l-6.57-16.96zm-7.258 0H10.172L16.74 20.48h-3.603l-1.326-3.63H5.862l-1.326 3.63H.933L6.569 3.52zm-.223 10.58h4.125L8.407 8.2l-2.061 5.9z" />
    </svg>
  );
}
