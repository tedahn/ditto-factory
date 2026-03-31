import { cn } from "@/lib/utils";

interface DittoLogoProps {
  className?: string;
}

export function DittoLogo({ className }: DittoLogoProps) {
  return (
    <svg
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={cn("h-6 w-6", className)}
      aria-label="Ditto logo"
    >
      {/* Body — smoothed traced outline: wide irregular blob with bumps at bottom */}
      <path
        d="M32 8
           C37 8 43 11 48 17
           C52 21 56 25 57 30
           C58 35 56 40 57 45
           C58 50 54 54 48 54
           C44 54 42 50 38 52
           C34 55 30 55 26 53
           C22 51 18 54 14 52
           C8 49 4 44 5 40
           C6 36 9 32 11 28
           C12 24 11 18 14 14
           C18 10 24 8 32 8Z"
        fill="#B8A9D4"
      />
      {/* Sheen — subtle highlight */}
      <ellipse cx="27" cy="20" rx="10" ry="6" fill="#CFC3E8" opacity="0.45" />

      {/* Left eye — small dot */}
      <circle cx="29" cy="28" r="1.4" fill="#3B2D5E" />

      {/* Right eye — small dot */}
      <circle cx="36" cy="28" r="1.4" fill="#3B2D5E" />

      {/* Mouth — subtle short smile */}
      <path
        d="M27 33 Q32 36 37 33"
        stroke="#3B2D5E"
        strokeWidth="1.2"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}
