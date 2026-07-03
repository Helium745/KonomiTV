<template>
    <v-dialog max-width="600" :model-value="modelValue" @update:model-value="(value) => emit('update:modelValue', value)">
        <v-card class="timeshift-save-dialog">
            <v-card-title class="d-flex justify-center pt-6 font-weight-bold">この番組を保存しますか？</v-card-title>
            <v-card-text class="pt-2 pb-0">
                <div class="timeshift-save-dialog__record-title">{{record.title}}</div>
                <div class="timeshift-save-dialog__record-range">
                    {{dayjs(record.start_time).format('MM/DD (dd) HH:mm')}} 〜 {{dayjs(record.end_time).format('HH:mm')}}
                </div>
                <div class="timeshift-save-dialog__description">
                    リングバッファ上のタイムシフト録画は、上書きされると失われます。<br>
                    保存すると、録画フォルダに TS ファイルとして書き出され、通常の録画番組と同じように一覧に表示されます。
                </div>
                <div v-if="is_record_too_short" class="timeshift-save-dialog__error mt-3">
                    この録画は{{MINIMUM_SAVE_DURATION_SECONDS}}秒未満のため保存できません。
                </div>
            </v-card-text>
            <v-card-actions class="pt-4 px-6 pb-6">
                <v-spacer></v-spacer>
                <v-btn color="text" variant="text" :disabled="is_saving" @click="emit('update:modelValue', false)">
                    <Icon icon="fluent:dismiss-20-regular" width="18px" height="18px" />
                    <span class="ml-1">キャンセル</span>
                </v-btn>
                <v-btn class="px-3" color="secondary" variant="flat"
                    :disabled="is_record_too_short" :loading="is_saving" @click="save">
                    <Icon icon="fluent:save-20-regular" width="18px" height="18px" />
                    <span class="ml-1">保存</span>
                </v-btn>
            </v-card-actions>
        </v-card>
    </v-dialog>
</template>
<script lang="ts" setup>

import { ref, computed } from 'vue';

import Message from '@/message';
import Timeshift, { ITimeshiftSaveTarget } from '@/services/Timeshift';
import { dayjs } from '@/utils';

// 保存を許可する最小の番組長 (秒)
// サーバー側 (TimeshiftSaveTask.MINIMUM_SAVE_DURATION_SECONDS) と同じ値
// これより短いと、保存自体は成功してもメタデータ解析後に「短すぎる録画」として一覧に表示されなくなる
const MINIMUM_SAVE_DURATION_SECONDS = 70;


// Props
const props = defineProps<{
    modelValue: boolean;
    record: ITimeshiftSaveTarget;
}>();

// Emits
const emit = defineEmits<{
    (e: 'update:modelValue', value: boolean): void;
    (e: 'saved'): void;
}>();

// 保存処理中かどうか
const is_saving = ref(false);

// record 自体の長さが最小保存時間に満たないかどうか
const is_record_too_short = computed(() => {
    return dayjs(props.record.end_time).diff(dayjs(props.record.start_time), 'second') < MINIMUM_SAVE_DURATION_SECONDS;
});

// 保存を実行する
const save = async () => {
    if (is_record_too_short.value) {
        return;
    }

    is_saving.value = true;
    const result = await Timeshift.saveTimeshiftRecord(props.record.recorder_id, props.record.id);
    is_saving.value = false;

    if (result !== null) {
        Message.success('保存を開始しました。完了すると録画番組一覧に表示されます。');
        emit('update:modelValue', false);
        emit('saved');
    }
};

</script>
<style lang="scss" scoped>

.timeshift-save-dialog {

    &__record-title {
        font-size: 15px;
        font-weight: bold;
        color: rgb(var(--v-theme-text));
    }

    &__record-range {
        margin-top: 4px;
        font-size: 13px;
        color: rgb(var(--v-theme-text-darken-1));
    }

    &__description {
        margin-top: 16px;
        font-size: 13px;
        line-height: 1.7;
        color: rgb(var(--v-theme-text-darken-1));
    }

    &__error {
        font-size: 13px;
        color: rgb(var(--v-theme-error));
    }
}

</style>
