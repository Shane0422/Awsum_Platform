<script>
  import { onMount } from "svelte";

  let user = null;
  let error = "";

  onMount(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      window.location.href = "/login";
      return;
    }

    try {
      // ✅ 토큰 Payload 디코딩
      const payload = JSON.parse(atob(token.split(".")[1]));
      user = {
        storeId: payload.store_code,
        email: payload.sub,
        role: payload.role_code
      };
    } catch (err) {
      error = "세션 만료 또는 잘못된 토큰입니다.";
      localStorage.removeItem("token");
      window.location.href = "/login";
    }
  });

  function handleLogout() {
    localStorage.removeItem("token");
    window.location.href = "/login";
  }
</script>

<div class="min-h-screen flex flex-col bg-gray-100">
  <!-- ✅ Top Navbar -->
  <header class="bg-white shadow px-6 py-4 flex justify-between items-center">
    <h1 class="text-xl font-bold text-indigo-600">Tuxedo Rental Dashboard</h1>
    <button on:click={handleLogout}
      class="bg-red-500 text-white px-4 py-1 rounded-lg hover:bg-red-600 transition">
      로그아웃
    </button>
  </header>

  <!-- ✅ Main Content -->
  <main class="flex-1 p-6">
    {#if error}
      <div class="bg-red-100 text-red-600 p-3 rounded">{error}</div>
    {:else if user}
      <div class="bg-white p-6 rounded-2xl shadow-lg">
        <h2 class="text-lg font-semibold text-gray-800 mb-4">환영합니다!</h2>
        <p class="text-gray-600 mb-2"><b>Store ID:</b> {user.storeId}</p>
        <p class="text-gray-600 mb-2"><b>Email:</b> {user.email}</p>
        <p class="text-gray-600"><b>Role:</b> {user.role}</p>
      </div>

      <!-- ✅ Quick Actions -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-6 mt-8">
        <a href="/orders" class="bg-indigo-500 text-white rounded-xl p-6 text-center shadow hover:bg-indigo-600 transition">
          📦 주문 관리
        </a>
        <a href="/customers" class="bg-green-500 text-white rounded-xl p-6 text-center shadow hover:bg-green-600 transition">
          👥 고객 관리
        </a>
        <a href="/products" class="bg-yellow-500 text-white rounded-xl p-6 text-center shadow hover:bg-yellow-600 transition">
          🎩 턱시도 관리
        </a>
        <a href="/reports" class="bg-purple-500 text-white rounded-xl p-6 text-center shadow hover:bg-purple-600 transition">
          📊 보고서
        </a>
      </div>
    {/if}
  </main>
</div>
