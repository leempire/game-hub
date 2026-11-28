// Original atlas: docs/design/潮汐鱼群-鱼种图集.png. Rebuild: bin/prepare_fish_sprites.py.
export const fishSprites = {
  tetra: require('../assets/fish/tetra.webp'),
  goldfish: require('../assets/fish/goldfish.webp'),
  clownfish: require('../assets/fish/clownfish.webp'),
  tang: require('../assets/fish/tang.webp'),
  flounder: require('../assets/fish/flounder.webp'),
  puffer: require('../assets/fish/puffer.webp'),
  lionfish: require('../assets/fish/lionfish.webp'),
  angelfish: require('../assets/fish/angelfish.webp'),
  tuna: require('../assets/fish/tuna.webp'),
  swordfish: require('../assets/fish/swordfish.webp'),
  hammerhead: require('../assets/fish/hammerhead.webp'),
  shark: require('../assets/fish/shark.webp'),
};
export type FishSpecies = keyof typeof fishSprites;

// Cosmetic size bands only. Actual radius and edible/threat status come from the server.
export function fishSpecies(radius: number, id: number): FishSpecies {
  const variants: readonly FishSpecies[] = radius < 12 ? ['tetra']
    : radius < 20 ? ['goldfish', 'clownfish']
    : radius < 30 ? ['tang', 'angelfish']
    : radius < 40 ? ['flounder', 'puffer']
    : radius < 53 ? ['lionfish', 'tuna']
    : radius < 70 ? ['tuna', 'swordfish']
    : radius < 90 ? ['hammerhead', 'shark'] : ['shark'];
  return variants[Math.abs(id) % variants.length];
}
