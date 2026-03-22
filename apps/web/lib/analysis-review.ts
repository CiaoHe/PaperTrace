import type { ReviewClaimIndexEntry, ReviewFileEntry, ReviewManifest } from "@papertrace/contracts";

export const REVIEW_BUCKET_ORDER = [
  { key: "primary", label: "Primary", emptyMessage: "No primary comparable files are ready for review." },
  { key: "added", label: "Added", emptyMessage: "No added files in this review artifact." },
  { key: "deleted", label: "Deleted", emptyMessage: "No deleted files in this review artifact." },
  { key: "ambiguous", label: "Ambiguous", emptyMessage: "No ambiguous file matches were detected." },
  { key: "low_confidence", label: "Low Confidence", emptyMessage: "No low-confidence file matches were detected." },
  { key: "other_languages", label: "Other Languages", emptyMessage: "No secondary-language files were captured." },
  { key: "large_files", label: "Large Files", emptyMessage: "No large-file fallback artifacts were needed." },
] as const;

export type ReviewBucketKey = (typeof REVIEW_BUCKET_ORDER)[number]["key"];

export interface ReviewBucketDescriptor {
  key: ReviewBucketKey;
  label: string;
  count: number;
  files: ReviewFileEntry[];
  emptyMessage: string;
}

export interface ReviewTreeNode {
  name: string;
  path: string;
  isFile: boolean;
  fileId: string | null;
  changedCount: number;
  children: ReviewTreeNode[];
}

export function reviewBuckets(manifest: ReviewManifest): ReviewBucketDescriptor[] {
  return REVIEW_BUCKET_ORDER.map((bucket) => {
    const files =
      bucket.key === "primary" ? manifest.review_queue : (manifest.secondary_buckets[bucket.key]?.files ?? []);
    const count =
      bucket.key === "primary" ? manifest.review_queue.length : (manifest.secondary_buckets[bucket.key]?.count ?? 0);
    return {
      key: bucket.key,
      label: bucket.label,
      count,
      files,
      emptyMessage: bucket.emptyMessage,
    };
  });
}

export function initialReviewSelection(manifest: ReviewManifest): {
  bucketKey: ReviewBucketKey;
  fileId: string | null;
} {
  return {
    bucketKey: "primary",
    fileId: manifest.review_queue[0]?.file_id ?? null,
  };
}

export function buildReviewFileTree(files: ReviewFileEntry[]): ReviewTreeNode[] {
  const root: ReviewTreeNode[] = [];

  const upsert = (nodes: ReviewTreeNode[], parts: string[], entry: ReviewFileEntry, prefix = ""): void => {
    const [head, ...rest] = parts;
    if (!head) {
      return;
    }
    const path = `${prefix}/${head}`.replace(/^\/+/, "");
    let node = nodes.find((candidate) => candidate.path === path) ?? null;
    const isFile = rest.length === 0;
    if (!node) {
      node = {
        name: head,
        path,
        isFile,
        fileId: null,
        changedCount: 0,
        children: [],
      };
      nodes.push(node);
    }
    if (isFile) {
      node.fileId = entry.file_id;
      node.changedCount = Math.max(1, entry.stats?.changed_line_count ?? 0);
      return;
    }
    upsert(node.children, rest, entry, path);
    node.changedCount = node.children.reduce((sum, child) => sum + child.changedCount, 0);
  };

  for (const entry of files) {
    const filePath = entry.current_path ?? entry.source_path;
    if (!filePath) {
      continue;
    }
    upsert(root, filePath.split("/"), entry);
  }

  return sortTree(root);
}

export function ancestorPaths(filePath: string | null): Set<string> {
  if (!filePath) {
    return new Set();
  }
  const parts = filePath.split("/");
  const expanded = new Set<string>();
  for (let index = 0; index < parts.length - 1; index += 1) {
    expanded.add(parts.slice(0, index + 1).join("/"));
  }
  return expanded;
}

export function findFileById(manifest: ReviewManifest, fileId: string | null): ReviewFileEntry | null {
  if (!fileId) {
    return null;
  }
  for (const bucket of reviewBuckets(manifest)) {
    const match = bucket.files.find((entry) => entry.file_id === fileId);
    if (match) {
      return match;
    }
  }
  return null;
}

export function findSelectionForClaim(
  manifest: ReviewManifest,
  claimId: string,
): { bucketKey: ReviewBucketKey; fileId: string } | null {
  for (const bucket of reviewBuckets(manifest)) {
    const match = bucket.files.find((entry) => (entry.linked_claim_ids ?? []).includes(claimId));
    if (match) {
      return { bucketKey: bucket.key, fileId: match.file_id };
    }
  }
  return null;
}

export function claimById(claims: ReviewClaimIndexEntry[], claimId: string | null): ReviewClaimIndexEntry | null {
  if (!claimId) {
    return null;
  }
  return claims.find((claim) => claim.claim_id === claimId) ?? null;
}

function sortTree(nodes: ReviewTreeNode[]): ReviewTreeNode[] {
  return [...nodes]
    .map((node) => ({
      ...node,
      children: sortTree(node.children),
    }))
    .sort((left, right) => {
      if (left.isFile !== right.isFile) {
        return left.isFile ? 1 : -1;
      }
      return left.name.localeCompare(right.name);
    });
}
