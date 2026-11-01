<script lang="ts">
  interface Props { imageUrl?: string | null; size?: number; name?: string; }
  let { imageUrl = null, size = 36, name = 'User' }: Props = $props();
  let source = $state(imageUrl);
  let failed = $state(false);
  export function setImage(url: string | null) { source = url; failed = false; }
</script>

<span class="awsum-avatar" style:width={`${size}px`} style:height={`${size}px`} title={name}>
  {#if source && !failed}
    <img src={source} alt="" onerror={() => { failed = true; }} />
  {:else}
    <img class="awsum-avatar-default" src="/images/platform/defaults/default-user.svg" alt="" />
  {/if}
</span>

<style>
  .awsum-avatar { display: inline-flex; vertical-align: middle; align-items: center; justify-content: center; flex-shrink: 0; border-radius: 50%; overflow: hidden; background: #eaf0f8; color: #657d9c; }
  img { width: 100%; height: 100%; object-fit: cover; }
</style>
