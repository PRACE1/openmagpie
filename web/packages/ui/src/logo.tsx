import clsx from "clsx";
import Image from "next/image";

export interface EmblemProps {
  /** Pixel size of the rendered square. */
  size?: number;
  className?: string;
}

/**
 * The 400x400 Signal-disc + Paper-sparkle emblem. Single source of truth,
 * works on light, dark, and photographic backgrounds (the paper sparkle reads
 * fine on every viable surface).
 */
export function Emblem({ size = 48, className }: EmblemProps) {
  return (
    <Image
      src="/brand/emblem.svg"
      width={size}
      height={size}
      alt="OpenMagpie emblem"
      priority
      className={className}
    />
  );
}

export interface LogoProps {
  /** Height in pixels (width scales proportionally). */
  height?: number;
  /** Background context; controls light/dark wordmark variant. */
  on?: "light" | "dark";
  className?: string;
}

/**
 * The full "OpenMagpie" wordmark, emblem + Poppins 600 set text.
 */
export function Logo({ height = 32, on = "light", className }: LogoProps) {
  const src =
    on === "light" ? "/brand/wordmark-on-light.svg" : "/brand/wordmark-on-dark.svg";
  // Wordmark viewBox is 1029.53x195.48 (~5.27:1).
  const width = Math.round(height * (1029.53 / 195.48));
  return (
    <Image
      src={src}
      width={width}
      height={height}
      alt="OpenMagpie"
      priority
      className={clsx("h-auto", className)}
    />
  );
}

export interface MascotProps {
  size?: number;
  className?: string;
}

/**
 * The illustrated magpie holding a gem, used as a hero / accent visual.
 */
export function Mascot({ size = 240, className }: MascotProps) {
  return (
    <Image
      src="/brand/mascot.png"
      width={size}
      height={size}
      alt="The OpenMagpie magpie holding a signal gem"
      priority
      className={className}
    />
  );
}
