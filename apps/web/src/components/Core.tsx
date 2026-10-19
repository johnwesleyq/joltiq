/** JoltIQ's face: a living energy core. Spins slowly at rest, fast when it's thinking or acting. */
export default function Core({ size = 36, thinking = false, className = "" }: { size?: number; thinking?: boolean; className?: string }) {
  return <span aria-hidden className={`core inline-block shrink-0 ${thinking ? "core-think" : "core-spin"} ${className}`} style={{ width: size, height: size }} />;
}
