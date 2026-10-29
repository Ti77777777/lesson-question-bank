/* Pure pagination helper. Browser measurements are estimates; TeX owns final pagination. */
export function paginateBlocks(blocks, capacity) {
  const pages = []; let page = [], height = 0;
  for (let i = 0; i < blocks.length; i++) {
    const block = blocks[i];
    const next = block.heading ? blocks[i + 1] : null;
    const needed = block.height + (next && next.height + block.height <= capacity ? next.height : 0);
    const explicitBreak = block.breakBefore && page.some(b => !b.heading);
    const headingBreak = block.heading && next?.breakBefore;
    if (page.length && (explicitBreak || headingBreak || height + needed > capacity)) {
      pages.push(page); page = []; height = 0;
    }
    page.push(block); height += block.height;
  }
  if (page.length) pages.push(page);
  return pages;
}
