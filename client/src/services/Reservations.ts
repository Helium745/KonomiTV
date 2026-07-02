
import APIClient from '@/services/APIClient';
import { IChannel } from '@/services/Channels';
import { IProgram } from '@/services/Programs';


/**
 * 録画設定 (mirakc RecordingOptions ベース)
 */
export interface IRecordSettings {
    priority: 1 | 2 | 3 | 4 | 5;
    pre_filters: string[];
    post_filters: string[];
}

/**
 * 録画予約情報
 */
export interface IReservation {
    id: number;
    channel: IChannel;
    program: IProgram;
    is_recording_in_progress: boolean;
    recording_availability: 'Full' | 'Partial' | 'Unavailable';
    comment: string;
    scheduled_recording_file_name: string;
    estimated_recording_file_size: number;
    record_settings: IRecordSettings;
    state: string;
    failed_reason: string | null;
}

/**
 * 録画予約情報リスト API のレスポンス
 */
export interface IReservations {
    total: number;
    reservations: IReservation[];
}

/**
 * 録画予約追加リクエスト
 */
export interface IReservationAddRequest {
    program_id: string;
    record_settings: IRecordSettings;
}

/**
 * 録画予約更新リクエスト
 */
export interface IReservationUpdateRequest {
    record_settings: IRecordSettings;
}

/**
 * 録画設定のデフォルト値
 */
export const IRecordSettingsDefault: IRecordSettings = {
    priority: 3,
    pre_filters: [],
    post_filters: [],
};


/**
 * 録画予約に関する API 操作を提供するクラス
 */
class Reservations {

    /**
     * すべての録画予約の情報を取得する
     * @returns 録画予約情報のリスト、取得失敗時は null
     */
    static async fetchReservations(): Promise<IReservations | null> {
        const response = await APIClient.get<IReservations>('/recording/reservations');

        if (response.type === 'error') {
            APIClient.showGenericError(response, '録画予約一覧を取得できませんでした。');
            return null;
        }

        return response.data;
    }

    /**
     * 指定された録画予約の情報を取得する
     * @param reservation_id 録画予約 ID
     * @returns 録画予約情報、取得失敗時は null
     */
    static async fetchReservation(reservation_id: number): Promise<IReservation | null> {
        const response = await APIClient.get<IReservation>(`/recording/reservations/${reservation_id}`);

        if (response.type === 'error') {
            APIClient.showGenericError(response, '録画予約情報を取得できませんでした。');
            return null;
        }

        return response.data;
    }

    /**
     * 録画予約を追加する
     * @param program_id 録画予約を追加する番組の ID
     * @param record_settings 録画設定
     * @param program DB に番組が存在しない場合に使う補助番組情報
     * @returns 成功した場合は true、失敗した場合は false
     */
    static async addReservation(
        program_id: string,
        record_settings: IRecordSettings,
    ): Promise<boolean> {
        const request_data: IReservationAddRequest = {
            program_id,
            record_settings,
        };

        const response = await APIClient.post('/recording/reservations', request_data);

        if (response.type === 'error') {
            switch (response.data.detail) {
                case 'Reservation conditions API is being reimplemented. Please wait for the next update.':
                    APIClient.showGenericError(response, '録画予約機能は EDCB バックエンド選択時のみ利用できます。');
                    break;
                case 'Specified program was not found':
                    APIClient.showGenericError(response, '指定された番組が見つかりませんでした。');
                    break;
                case 'Specified channel was not found':
                    APIClient.showGenericError(response, '指定されたチャンネルが見つかりませんでした。');
                    break;
                case 'Specified program duration is unknown':
                    APIClient.showGenericError(response, 'この番組は放送時間が未定のため、録画予約できません。');
                    break;
                case 'Program payload does not match channel':
                    APIClient.showGenericError(response, '番組情報とチャンネル情報が一致しないため、録画予約できません。');
                    break;
                case 'Specified channel does not have transport_stream_id':
                    APIClient.showGenericError(response, 'チャンネル情報に TSID がないため、録画予約できません。');
                    break;
                case 'Specified program has already ended':
                    APIClient.showGenericError(response, 'すでに終了した番組のため、録画予約できません。');
                    break;
                case 'The same program_id is already reserved':
                    APIClient.showGenericError(response, 'この番組は既に録画予約されています。');
                    break;
                case 'Failed to add a recording reservation':
                    APIClient.showGenericError(response, '録画予約の追加に失敗しました。');
                    break;
                default:
                    APIClient.showGenericError(response, '録画予約の追加に失敗しました。');
                    break;
            }
            return false;
        }

        return true;
    }

    /**
     * 録画予約を更新する
     * @param reservation_id 更新する録画予約の ID
     * @param record_settings 更新する録画設定
     * @returns 成功した場合は更新された録画予約情報、失敗した場合は null
     */
    static async updateReservation(reservation_id: number, record_settings: IRecordSettings): Promise<IReservation | null> {
        const request_data: IReservationUpdateRequest = {
            record_settings,
        };

        const response = await APIClient.put<IReservation>(`/recording/reservations/${reservation_id}`, request_data);

        if (response.type === 'error') {
            switch (response.data.detail) {
                case 'Reservation conditions API is being reimplemented. Please wait for the next update.':
                    APIClient.showGenericError(response, '録画予約機能は EDCB バックエンド選択時のみ利用できます。');
                    break;
                case 'Specified reservation_id was not found':
                    APIClient.showGenericError(response, '指定された録画予約が見つかりませんでした。');
                    break;
                case 'Failed to update the specified recording reservation':
                    APIClient.showGenericError(response, '録画予約の更新に失敗しました。');
                    break;
                default:
                    APIClient.showGenericError(response, '録画予約の更新に失敗しました。');
                    break;
            }
            return null;
        }

        return response.data;
    }

    /**
     * 録画予約を削除する
     * @param reservation_id 削除する録画予約の ID
     * @returns 成功した場合は true、失敗した場合は false
     */
    static async deleteReservation(reservation_id: number): Promise<boolean> {
        const response = await APIClient.delete<void>(`/recording/reservations/${reservation_id}`);

        if (response.type === 'error') {
            switch (response.data.detail) {
                case 'Reservation conditions API is being reimplemented. Please wait for the next update.':
                    APIClient.showGenericError(response, '録画予約機能は EDCB バックエンド選択時のみ利用できます。');
                    break;
                case 'Specified reservation_id was not found':
                    APIClient.showGenericError(response, '指定された録画予約が見つかりませんでした。');
                    break;
                case 'Failed to delete the specified recording reservation':
                    APIClient.showGenericError(response, '録画予約の削除に失敗しました。');
                    break;
                default:
                    APIClient.showGenericError(response, `録画予約 (ID: ${reservation_id}) の削除に失敗しました。`);
                    break;
            }
            return false;
        }

        return true;
    }

    /**
     * デフォルトの録画設定を返す (mirakc 移行後はプリセット API が不要のためデフォルト値を直接返す)
     * @returns デフォルトの録画設定
     */
    static fetchDefaultRecordSettings(): IRecordSettings {
        return structuredClone(IRecordSettingsDefault);
    }
}

export default Reservations;
