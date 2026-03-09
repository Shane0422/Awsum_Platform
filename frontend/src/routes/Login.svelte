<script>
  let storeId = "";
  let email = "";
  let password = "";
  let error = "";

  async function handleLogin() {
    error = "";

    if (!storeId || !email || !password) {
      error = "Please fill in all fields.";  
     // 모든 입력 필드를 입력하지 않았을 때 경고
      return;
    }

    try {
      const res = await fetch("http://127.0.0.1:8000/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ store_id: storeId, email, password })
      });

      if (!res.ok) {
        error = "Login failed. Check Store ID, Email, and Password.";
       // 로그인 실패 시 사용자에게 안내
        return;
      }

      const data = await res.json();
      // 로그인 성공 시 서버에서 받은 JWT 토큰 저장
      localStorage.setItem("token", data.access_token);

      // Dashboard 화면으로 이동
      window.location.href = "/dashboard";

    } catch (err) {
      error = "Server connection error: " + err.message;
      // 서버 연결 실패 시 표시
    }
  }
</script>

<div class="min-h-screen flex items-center justify-center bg-gray-100">
  <div class="bg-white p-8 rounded-2xl shadow-xl w-full max-w-md">
    <h1 class="text-2xl font-bold text-center mb-6 text-gray-800">Tuxedo Rental Login</h1>

    {#if error}
      <div class="bg-red-100 text-red-600 p-3 rounded mb-4 text-sm">{error}</div>
    {/if}

    <form on:submit|preventDefault={handleLogin} class="space-y-4">
      <div>
        <label for="storeId" class="block text-gray-600 text-sm font-medium mb-1">Store ID</label>
        <input id="storeId" type="text" bind:value={storeId} placeholder="Ex: 1001"
          class="w-full border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-400 focus:outline-none" />
      </div>

      <div>
        <label for="email" class="block text-gray-600 text-sm font-medium mb-1">Email</label>
        <input id="email" type="email" bind:value={email} placeholder="you@example.com"
          class="w-full border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-400 focus:outline-none" />
      </div>

      <div>
        <label for="password" class="block text-gray-600 text-sm font-medium mb-1">Password</label>
        <input id="password" type="password" bind:value={password} placeholder="********"
          class="w-full border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-400 focus:outline-none" />
      </div>

      <button type="submit"
        class="w-full bg-indigo-600 text-white py-2 rounded-lg font-medium hover:bg-indigo-700 transition">
        Login
      </button>
    </form>

    <p class="text-center text-sm text-gray-500 mt-4">
      Forgot your password? <a href="#" class="text-indigo-600 hover:underline">Reset</a>
    </p>
  </div>
</div>
