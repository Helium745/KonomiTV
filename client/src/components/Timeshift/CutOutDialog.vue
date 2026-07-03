<template>
    <v-dialog max-width="600" :model-value="modelValue" @update:model-value="(value) => emit('update:modelValue', value)">
        <v-card class="timeshift-cutout-dialog">
            <v-card-title class="d-flex justify-center pt-6 font-weight-bold">時間を指定して切り出す</v-card-title>
            <v-card-text class="pt-2 pb-0">
                <div class="timeshift-cutout-dialog__description">
                    番組の区切りとは関係なく、{{recorderTitle}}の録画内容から絶対時刻で範囲を指定して切り出せます。<br>
                    指定範囲が複数の番組にまたがる場合も、まとめて1本のファイルとして保存されます。
                </div>
                <div class="timeshift-cutout-dialog__buffer-range">
                    保存可能な範囲: {{dayjs(bufferStartTime).format('MM/DD (dd) HH:mm')}} 〜 {{dayjs(bufferEndTime).format('MM/DD (dd) HH:mm')}}
                </div>
                <div class="timeshift-cutout-dialog__range mt-5">
                    <v-text-field class="timeshift-cutout-dialog__range-input" v-model="start_datetime_str"
                        type="datetime-local" label="開始日時" variant="outlined" density="comfortable"
                        :min="buffer_start_str" :max="buffer_end_str" hide-details />
                    <span class="timeshift-cutout-dialog__range-separator">〜</span>
                    <v-text-field class="timeshift-cutout-dialog__range-input" v-model="end_datetime_str"
                        type="datetime-local" label="終了日時" variant="outlined" density="comfortable"
                        :min="buffer_start_str" :max="buffer_end_str" hide-details />
                </div>
                <div v-if="!is_valid_range" class="timeshift-cutout-dialog__error mt-3">
                    保存範囲が不正です。開始日時は終了日時より前で、保存可能な範囲内かつ{{MINIMUM_SAVE_DURATION_SECONDS}}秒以上である必要があります。
                </div>
            </v-card-text>
            <v-card-actions class="pt-4 px-6 pb-6">
                <v-spacer></v-spacer>
                <v-btn color="text" variant="text" :disabled="is_saving" @click="emit('update:modelValue', false)">
                    <Icon icon="fluent:dismiss-20-regular" width="18px" height="18px" />
                    <span class="ml-1">キャンセル</span>
                </v-btn>
                <v-btn class="px-3" color="secondary" variant="flat"
                    :disabled="!is_valid_range" :loading="is_saving" @click="save">
                    <Icon icon="fluent:cut-20-regular" width="18px" height="18px" />
                    <span class="ml-1">切り出して保存</span>
                </v-btn>
            </v-card-actions>
        </v-card>
    </v-dialog>
</template>
<script lang="ts" setup>

import { computed, ref, watch } from 'vue';

import type { Dayjs } from 'dayjs';

import Message from '@/message';
import Timeshift from '@/services/Timeshift';
import { dayjs } from '@/utils';


// 保存を許可する最小の範囲長 (秒)
// サーバー側 (TimeshiftSaveTask.MINIMUM_SAVE_DURATION_SECONDS) と同じ値
const MINIMUM_SAVE_DURATION_SECONDS = 70;

// 開始日時の初期値に使う、終了日時からのデフォルトの遡り幅
const DEFAULT_RANGE_MINUTES = 60;

// datetime-local input が要求する "YYYY-MM-DDTHH:mm" 形式にフォーマットする
const toDatetimeLocalString = (value: Dayjs): string => value.format('YYYY-MM-DDTHH:mm');


// Props
const props = defineProps<{
    modelValue: boolean;
    recorderId: string;
    // 表示用のレコーダー名 (チャンネル名など)
    recorderTitle: string;
    // 保存可能な範囲の下限/上限 (レコーダーのリングバッファ全体の開始/終了時刻、ISO 文字列)
    bufferStartTime: string;
    bufferEndTime: string;
}>();

// Emits
const emit = defineEmits<{
    (e: 'update:modelValue', value: boolean): void;
    (e: 'saved'): void;
}>();

// 開始/終了日時 (Dayjs)
const start_datetime = ref<Dayjs>(dayjs());
const end_datetime = ref<Dayjs>(dayjs());
// 保存処理中かどうか
const is_saving = ref(false);

// ダイアログが開かれるたびに、直近 DEFAULT_RANGE_MINUTES 分をデフォルト範囲として初期化する
watch(() => props.modelValue, (opened) => {
    if (opened === true) {
        const buffer_end = dayjs(props.bufferEndTime);
        const buffer_start = dayjs(props.bufferStartTime);
        end_datetime.value = buffer_end;
        const default_start = buffer_end.subtract(DEFAULT_RANGE_MINUTES, 'minute');
        start_datetime.value = default_start.isBefore(buffer_start) ? buffer_start : default_start;
        is_saving.value = false;
    }
});

// datetime-local input 用の min/max 文字列
const buffer_start_str = computed(() => toDatetimeLocalString(dayjs(props.bufferStartTime)));
const buffer_end_str = computed(() => toDatetimeLocalString(dayjs(props.bufferEndTime)));

// 開始/終了日時の入力用算出プロパティ
const start_datetime_str = computed<string>({
    get: () => toDatetimeLocalString(start_datetime.value),
    set: (value) => { start_datetime.value = dayjs(value); },
});
const end_datetime_str = computed<string>({
    get: () => toDatetimeLocalString(end_datetime.value),
    set: (value) => { end_datetime.value = dayjs(value); },
});

// 保存範囲が妥当かどうか (開始 < 終了、保存可能な範囲内に収まっている、かつ最小保存時間以上あるか)
const is_valid_range = computed(() => {
    const buffer_start = dayjs(props.bufferStartTime);
    const buffer_end = dayjs(props.bufferEndTime);
    return start_datetime.value.isBefore(end_datetime.value)
        && start_datetime.value.isSameOrAfter(buffer_start)
        && end_datetime.value.isSameOrBefore(buffer_end)
        && end_datetime.value.diff(start_datetime.value, 'second') >= MINIMUM_SAVE_DURATION_SECONDS;
});

// 保存を実行する
const save = async () => {
    if (is_valid_range.value === false) {
        return;
    }

    is_saving.value = true;
    const result = await Timeshift.saveTimeshiftRange(props.recorderId, {
        start_time: start_datetime.value.toISOString(),
        end_time: end_datetime.value.toISOString(),
    });
    is_saving.value = false;

    if (result !== null) {
        Message.success('切り出し保存を開始しました。完了すると録画番組一覧に表示されます。');
        emit('update:modelValue', false);
        emit('saved');
    }
};

</script>
<style lang="scss" scoped>

.timeshift-cutout-dialog {

    &__description {
        font-size: 13px;
        line-height: 1.7;
        color: rgb(var(--v-theme-text-darken-1));
    }

    &__buffer-range {
        margin-top: 10px;
        font-size: 12.5px;
        color: rgb(var(--v-theme-text-darken-1));
    }

    &__range {
        display: flex;
        align-items: center;
        gap: 12px;
        flex-wrap: wrap;
    }

    &__range-input {
        flex: 1;
        min-width: 220px;
    }

    &__range-separator {
        flex-shrink: 0;
        color: rgb(var(--v-theme-text-darken-1));
    }

    &__error {
        font-size: 13px;
        color: rgb(var(--v-theme-error));
    }
}

</style>
