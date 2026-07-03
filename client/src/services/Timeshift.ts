
import APIClient from '@/services/APIClient';
import { IChannel } from '@/services/Channels';
import { IRecordedProgram, IRecordedProgramDefault, IRecordedVideoDefault } from '@/services/Videos';


/** タイムシフトレコーダー情報を表すインターフェース */
export interface ITimeshiftRecorder {
    recorder_id: string;
    channel: IChannel | null;
    network_id: number;
    service_id: number;
    is_recording: boolean;
    current_record_id: number | null;
    total_records: number;
    start_time: string;
    end_time: string;
    duration: number;
}

/** タイムシフトレコーダー情報のリストを表すインターフェース */
export interface ITimeshiftRecorders {
    total: number;
    timeshift_recorders: ITimeshiftRecorder[];
}

/** タイムシフト record 情報を表すインターフェース */
export interface ITimeshiftRecord {
    id: number;
    recorder_id: string;
    channel: IChannel | null;
    network_id: number;
    service_id: number;
    event_id: number;
    title: string;
    description: string;
    genres: { major: string; middle: string; }[];
    is_free: boolean;
    is_recording: boolean;
    start_time: string;
    end_time: string;
    duration: number;
    file_size: number;
    primary_audio_type: string;
    primary_audio_language: string;
}

/** タイムシフト record 情報のリストを表すインターフェース */
export interface ITimeshiftRecords {
    total: number;
    timeshift_records: ITimeshiftRecord[];
}

/** SaveDialog が保存対象の特定に必要とする ITimeshiftRecord のサブセット */
export type ITimeshiftSaveTarget = Pick<ITimeshiftRecord, 'id' | 'recorder_id' | 'title' | 'start_time' | 'end_time'>;

/** タイムシフト時間範囲保存リクエストを表すインターフェース (番組の区切りとは無関係に、絶対時刻で範囲を指定する) */
export interface ITimeshiftRangeSaveRequest {
    start_time: string;
    end_time: string;
}

/** タイムシフト保存ジョブ情報を表すインターフェース */
export interface ITimeshiftSaveJob {
    id: string;
    recorder_id: string;
    record_id: number | null;
    title: string;
    is_range_cut: boolean;
    start_time: string;
    end_time: string;
    status: 'Pending' | 'Running' | 'Completed' | 'Failed';
    progress: number;
    file_size_total: number;
    file_size_written: number;
    error_message: string | null;
    created_at: string;
}

/** タイムシフト保存ジョブ情報のリストを表すインターフェース */
export interface ITimeshiftSaveJobs {
    total: number;
    save_jobs: ITimeshiftSaveJob[];
}


class Timeshift {

    /**
     * タイムシフトレコーダー一覧を取得する
     * @returns タイムシフトレコーダー一覧情報 or 取得に失敗した場合は null
     */
    static async fetchTimeshiftRecorders(): Promise<ITimeshiftRecorders | null> {

        const response = await APIClient.get<ITimeshiftRecorders>('/timeshift/recorders');

        if (response.type === 'error') {
            APIClient.showGenericError(response, 'タイムシフトレコーダー一覧を取得できませんでした。');
            return null;
        }

        return response.data;
    }


    /**
     * タイムシフト record 一覧を取得する
     * @param recorder_id mirakc 上のタイムシフトレコーダー名
     * @returns タイムシフト record 一覧情報 or 取得に失敗した場合は null
     */
    static async fetchTimeshiftRecords(recorder_id: string): Promise<ITimeshiftRecords | null> {

        const response = await APIClient.get<ITimeshiftRecords>(`/timeshift/recorders/${recorder_id}/records`);

        if (response.type === 'error') {
            APIClient.showGenericError(response, 'タイムシフト録画一覧を取得できませんでした。');
            return null;
        }

        return response.data;
    }


    /**
     * タイムシフト record を取得する
     * @param recorder_id mirakc 上のタイムシフトレコーダー名
     * @param record_id mirakc 上のタイムシフト record ID
     * @returns タイムシフト record 情報 or 取得に失敗した場合は null
     */
    static async fetchTimeshiftRecord(recorder_id: string, record_id: number): Promise<ITimeshiftRecord | null> {

        const response = await APIClient.get<ITimeshiftRecord>(`/timeshift/recorders/${recorder_id}/records/${record_id}`);

        if (response.type === 'error') {
            APIClient.showGenericError(response, 'タイムシフト録画情報を取得できませんでした。');
            return null;
        }

        return response.data;
    }


    /**
     * タイムシフト record (= 1番組) をまるごと録画フォルダへ恒久保存する
     * @param recorder_id mirakc 上のタイムシフトレコーダー名
     * @param record_id mirakc 上のタイムシフト record ID
     * @returns 作成された保存ジョブ or 失敗した場合は null
     */
    static async saveTimeshiftRecord(recorder_id: string, record_id: number): Promise<ITimeshiftSaveJob | null> {

        const response = await APIClient.post<ITimeshiftSaveJob>(`/timeshift/recorders/${recorder_id}/records/${record_id}/save`);

        if (response.type === 'error') {
            APIClient.showGenericError(response, 'タイムシフト録画の保存を開始できませんでした。');
            return null;
        }

        return response.data;
    }


    /**
     * レコーダーのリングバッファ全体から、番組の区切りとは無関係に絶対時刻で範囲を切り出して録画フォルダへ保存する
     * @param recorder_id mirakc 上のタイムシフトレコーダー名
     * @param save_request 保存する絶対時刻の範囲
     * @returns 作成された保存ジョブ or 失敗した場合は null
     */
    static async saveTimeshiftRange(recorder_id: string, save_request: ITimeshiftRangeSaveRequest): Promise<ITimeshiftSaveJob | null> {

        const response = await APIClient.post<ITimeshiftSaveJob>(`/timeshift/recorders/${recorder_id}/save-range`, save_request);

        if (response.type === 'error') {
            APIClient.showGenericError(response, 'タイムシフト録画の切り出し保存を開始できませんでした。');
            return null;
        }

        return response.data;
    }


    /**
     * タイムシフト保存ジョブの一覧を取得する
     * @returns タイムシフト保存ジョブ一覧情報 or 取得に失敗した場合は null
     */
    static async fetchTimeshiftSaveJobs(): Promise<ITimeshiftSaveJobs | null> {

        const response = await APIClient.get<ITimeshiftSaveJobs>('/timeshift/saves');

        if (response.type === 'error') {
            APIClient.showGenericError(response, 'タイムシフト保存ジョブ一覧を取得できませんでした。');
            return null;
        }

        return response.data;
    }


    /**
     * タイムシフト record を、プレイヤー (PlayerController) が扱える IRecordedProgram 形式に変換する
     * タイムシフト録画は DB に保存されないため、視聴用の PlayerController は通常の録画番組と同じ IRecordedProgram 形式を要求する
     * @param record タイムシフト record 情報
     * @returns IRecordedProgram 形式に変換した情報
     */
    static convertToRecordedProgram(record: ITimeshiftRecord): IRecordedProgram {
        return {
            ...IRecordedProgramDefault,
            id: record.id,
            channel: record.channel,
            network_id: record.network_id,
            service_id: record.service_id,
            event_id: record.event_id,
            title: record.title,
            description: record.description,
            start_time: record.start_time,
            end_time: record.end_time,
            duration: record.duration,
            is_free: record.is_free,
            genres: record.genres,
            primary_audio_type: record.primary_audio_type,
            primary_audio_language: record.primary_audio_language,
            recorded_video: {
                ...IRecordedVideoDefault,
                file_size: record.file_size,
                duration: record.duration,
                recording_start_time: record.start_time,
                recording_end_time: record.end_time,
            },
        };
    }
}

export default Timeshift;
