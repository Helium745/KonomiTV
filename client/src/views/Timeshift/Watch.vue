<template>
    <Watch :playback_mode="'Video'" />
</template>
<script lang="ts">

import { mapStores } from 'pinia';
import { defineComponent } from 'vue';

import Watch from '@/components/Watch/Watch.vue';
import PlayerController from '@/services/player/PlayerController';
import Timeshift from '@/services/Timeshift';
import usePlayerStore from '@/stores/PlayerStore';
import useSettingsStore from '@/stores/SettingsStore';

// PlayerController のインスタンス
// data() 内に記述すると再帰的にリアクティブ化され重くなる上リアクティブにする必要自体がないので、グローバル変数にしている
let player_controller: PlayerController | null = null;

export default defineComponent({
    name: 'Timeshift-Watch',
    components: {
        Watch,
    },
    computed: {
        ...mapStores(usePlayerStore, useSettingsStore),
    },
    // 開始時に実行
    created() {

        // 下記以外の視聴画面の開始処理は Watch コンポーネントの方で自動的に行われる

        // 再生セッションを初期化
        this.init();
    },
    // レコーダー/record 切り替え時に実行
    // コンポーネント（インスタンス）は再利用される
    // ref: https://v3.router.vuejs.org/ja/guide/advanced/navigation-guards.html#%E3%83%AB%E3%83%BC%E3%83%88%E5%8D%98%E4%BD%8D%E3%82%AB%E3%82%99%E3%83%BC%E3%83%88%E3%82%99
    beforeRouteUpdate(to, from, next) {

        // 前の再生セッションを破棄して終了し、完了を待ってから再度初期化する
        const destroy_promise = this.destroy();
        destroy_promise.then(() => this.init());

        // 次のルートに置き換え
        next();
    },
    // 終了前に実行
    beforeUnmount() {

        // destroy() を実行
        // 別のページへ遷移するため、DPlayer のインスタンスを確実に破棄する
        // さもなければ、ブラウザがリロードされるまでバックグラウンドで永遠に再生され続けてしまう
        this.destroy();

        // 上記以外の視聴画面の終了処理は Watch コンポーネントの方で自動的に行われる
    },
    methods: {

        // 再生セッションを初期化する
        async init() {

            // URL 上のレコーダー名・record ID が未定義なら実行しない (フェイルセーフ)
            // 基本あり得ないはずだが、念のため
            if (this.$route.params.recorder_id === undefined || this.$route.params.record_id === undefined) {
                this.$router.push({path: '/not-found/'});
                return;
            }

            // タイムシフト record 情報を取得する
            const recorder_id = this.$route.params.recorder_id as string;
            const record_id = parseFloat(this.$route.params.record_id as string);
            const record = await Timeshift.fetchTimeshiftRecord(recorder_id, record_id);
            if (record === null) {
                this.$router.push({path: '/not-found/'});
                return;
            }

            // PlayerController の Video モードが要求する IRecordedProgram 形式に変換してストアへ設定
            this.playerStore.recorded_program = Timeshift.convertToRecordedProgram(record);
            // タイムシフト録画であることと、mirakc 上のレコーダー名を PlayerController 側へ伝える
            this.playerStore.timeshift_recorder_id = recorder_id;

            // PlayerController を初期化
            player_controller = new PlayerController('Video');
            await player_controller.init();
        },

        // 再生セッションを破棄する
        // 再生するタイムシフト record を切り替える際にも実行される
        async destroy() {

            // PlayerController を破棄
            if (player_controller !== null) {
                await player_controller.destroy();
                player_controller = null;
            }
        }
    }
});

</script>
